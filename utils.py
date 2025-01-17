# Standard library imports
import hashlib
import os
import json
import configparser
from datetime import datetime

# Third-party imports
import magic
from PIL import Image
from sqlalchemy import or_

# Local application imports
from models import Batch, Tray, Bag, db


def water_volume_imperial(grams):
    ounces = grams / 29.5735  # 1 fl oz = 29.5735g water
    if ounces < 8:
        return f"{ounces:.1f}oz"
    cups = ounces / 8  # 8 fl oz = 1 cup
    if cups < 4:
        return f"{cups:.1f}cup"
    quarts = cups / 4  # 4 cups = 1 quart
    return f"{quarts:.1f}qt"


def water_volume_metric(grams):
    if grams >= 1000:
        return f"{grams/1000:.1f}L"
    return f"{grams:.0f}ml"

def search_batches(search_query, date_from, date_to):
    # Base query
    query = Batch.query.distinct()

    # Convert date strings to datetime objects
    if date_from:
        date_from = datetime.strptime(date_from, "%Y-%m-%d")
    if date_to:
        date_to = datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S")

    # Apply filters directly to base query first
    if date_from:
        query = query.filter(Batch.start_date >= date_from)
    if date_to:
        query = query.filter(Batch.start_date <= date_to)

    # Then apply text search if needed
    if search_query:
        try:
            search_id = int(search_query)
        except ValueError:
            search_id = None

        query = (
            query.outerjoin(Tray)
            .outerjoin(Bag)
            .filter(
                or_(
                    Batch.id == search_id,
                    Batch.notes.ilike(f"%{search_query}%"),
                    Tray.contents.ilike(f"%{search_query}%"),
                    Tray.notes.ilike(f"%{search_query}%"),
                    Bag.contents.ilike(f"%{search_query}%"),
                    Bag.notes.ilike(f"%{search_query}%"),
                )
            )
        )

    return query


def search_bags(search_query, date_from, date_to, unopened):
    # Base query starting from Bag model
    query = Bag.query.distinct()

    # Apply date filters to bag creation date
    if date_from:
        date_from = datetime.strptime(date_from, "%Y-%m-%d")
        query = query.filter(Bag.created_date >= date_from)
    if date_to:
        date_to = datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        query = query.filter(Bag.created_date <= date_to)

    # Add unopened filter
    if unopened:
        query = query.filter(Bag.consumed_date.is_(None))

    # Text search across bag fields
    if search_query:
        query = query.filter(
            or_(
                Bag.id.ilike(f"%{search_query}%"),
                Bag.contents.ilike(f"%{search_query}%"),
                Bag.location.ilike(f"%{search_query}%"),
                Bag.notes.ilike(f"%{search_query}%"),
            )
        )

    return query

def format_bytes_size(bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"

def weight_imperial(grams):
    ounces = grams / 28.3495  # 1 oz = 28.3495g
    if ounces < 16:
        return f"{ounces:.1f}oz"
    pounds = ounces / 16  # 16 oz = 1 pound
    return f"{pounds:.1f}lb"

def test_db_connection():
    try:
        # Test 1: Basic connection to check if MySQL server is running
        db.session.execute(db.text('SELECT 1'))
        
        # Test 2: Check if database exists by attempting to use it
        db.session.execute(db.text('SELECT DATABASE()'))
        
        # Test 3: Check user permissions with write test
        if db.session.is_active:
            db.session.rollback()
        db.session.begin()
        db.session.rollback()
        
        # Test 4: Verify schema matches by checking all required tables
        db.create_all()
        inspector = db.inspect(db.engine)
        required_tables = {'batch', 'tray', 'bag', 'photo'}
        existing_tables = set(inspector.get_table_names())
        missing_tables = required_tables - existing_tables
        if missing_tables:
            return False, f"Missing required table(s): {', '.join(missing_tables)}"
        return True, None
        
    except db.exc.OperationalError as e:
        if "Connection refused" in str(e):
            return False, f"MySQL server not running at configured host/port"
        elif "Access denied" in str(e):
            return False, f"Access denied for configured user"
        elif "Unknown database" in str(e):
            return False, f"Database does not exist"
        return False, str(e)
        
    except Exception as e:
        return False, str(e)

    finally:
        if db.session.is_active:
            db.session.rollback()

def format_list(items):
    if not items:
        return "nothing"
    if len(items) == 1:
        return str(items[0])
    return f"{', '.join(str(x) for x in items[:-1])} and {items[-1]}"


def cosine_similarity(v1, v2):
    dot_product = sum(x*y for x, y in zip(v1, v2))
    magnitude1 = sum(x*x for x in v1) ** 0.5
    magnitude2 = sum(x*x for x in v2) ** 0.5
    return dot_product / (magnitude1 * magnitude2)

def get_database_context(question, client):
    SIMILARITY_THRESHOLD = 0.8
    # Get relevant data from database
    batches = db.session.query(Batch).all()
    trays = db.session.query(Tray).all()
    bags = db.session.query(Bag).all()

    # Create text representations
    context_texts = []
    config = configparser.ConfigParser()
    config.read('config.ini')
    if 'openai' in config:
        context_texts.append(config['openai'].get('context', ''))

    for batch in batches:
        context_texts.append(f"Batch {batch.id} created on {batch.start_date.strftime('%Y-%m-%d')} "
        f"contains {format_list([tray.contents for tray in batch.trays])}. "
        f"Status: {batch.status}, Batch Notes: '{batch.notes}'")  

    tray_descriptions = []
    for t in trays:
        net_starting_weight = t.starting_weight - t.tare_weight
        net_ending_weight = (t.ending_weight - t.tare_weight) if t.ending_weight else None
        weight_info = f"started at {net_starting_weight}g"
        if net_ending_weight:
            weight_info += f", finished at {net_ending_weight}g"
        context_texts.append(
            f"Tray {t.id} at position {t.position} in batch {t.batch.id} contains {t.contents}, {weight_info}, Tray Notes: '{t.notes}'"
        )

    for bag in bags:
        created_date = bag.created_date.strftime("%Y-%m-%d")
        if bag.consumed_date:
            status = f"Consumed on {bag.consumed_date.strftime('%Y-%m-%d')}"
        else:
            status = "Not yet consumed"
        context_texts.append(f"Bag {bag.id} containing {bag.contents} was created from batch {bag.batch.id} on {created_date}, "
        f"Status: {status}, Storage Location: {bag.location}, Weight: {bag.weight}g, Bag Notes: '{bag.notes}'")

    print(f"context_texts: {context_texts}")

    # Get embeddings
    response = client.embeddings.create(
        model="text-embedding-ada-002",
        input=[question] + context_texts
    )
    
    # Find most relevant context using cosine similarity
    question_embedding = response.data[0].embedding
    context_embeddings = [item.embedding for item in response.data[1:]]
    
    # Calculate similarities and filter by threshold
    similarities = [cosine_similarity(question_embedding, ce) for ce in context_embeddings]
    context_with_scores = list(zip(similarities, context_texts))
    
    # Sort by similarity score in descending order
    sorted_contexts = sorted(context_with_scores, key=lambda x: x[0], reverse=True)
    print(f"sorted_contexts: {sorted_contexts}")
    
    # Filter contexts above threshold and take only the text portion
    relevant_contexts = [
        context for score, context in sorted_contexts 
        if score >= SIMILARITY_THRESHOLD
    ]
    if not relevant_contexts:
        relevant_contexts.append("No matching records found in the database.")

    print(f"contexts:")
    print("\n".join(relevant_contexts[:100]))
    return "\n".join(relevant_contexts[:100])
