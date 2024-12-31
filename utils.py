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


def calculate_backup_hash(db_path, image_files):
    hasher = hashlib.sha256()
    with open(db_path, "rb") as f:
        hasher.update(f.read())
    for image_file in sorted(image_files):
        with open(image_file, "rb") as f:
            hasher.update(f.read())
    return hasher.hexdigest()


def validate_backup_contents(zip_file):
    """Validate all files in the backup archive"""
    valid_files = {"freezedry.db", "manifest.json"}
    uploads_prefix = "static/uploads/"

    # Check that all required files exist
    found_files = set(name for name in zip_file.namelist() if "/" not in name)
    missing_files = valid_files - found_files
    if missing_files:
        return False, f"Missing required files: {', '.join(missing_files)}"

    # Validate each file
    for file_path in zip_file.namelist():
        if file_path == "freezedry.db":
            content = zip_file.read(file_path)
            mime = magic.from_buffer(content, mime=True)
            # Valid SQLite MIME types across different platforms
            if mime not in {
                "application/x-sqlite3",
                "application/vnd.sqlite3",
                "application/sqlite3",
                "application/x-sqlite",
                "application/db",
                "application/sqlite"
            }:
                return False, f"Invalid database file format '{mime}'"

        elif file_path == "manifest.json":
            continue

        elif file_path.startswith(uploads_prefix) and file_path.lower().endswith(".webp"):
            content = zip_file.read(file_path)
            mime = magic.from_buffer(content, mime=True)
            if mime not in ("image/webp"):
                return False, f"Invalid image format: {file_path}"

        else:
            return False, f"Unexpected file in backup: {file_path}"

    return True, None


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
