# Standard library imports
import hashlib
import os
from datetime import datetime

# Third-party imports
import magic
from PIL import Image
from sqlalchemy import or_

# Local application imports
from models import Batch, Tray, Bag


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
