try:
    # Standard library imports
    import configparser
    import hashlib
    import json
    import os
    import re
    import shutil
    from datetime import datetime, UTC, timedelta
    from io import BytesIO
    from urllib.parse import urlparse
    from zipfile import ZipFile
    from openai import OpenAI
    from flask import session

    # Third-party imports
    import magic
    import qrcode
    from flask import (
        Flask,
        current_app,
        flash,
        redirect,
        render_template,
        request,
        send_file,
        send_from_directory,
        url_for
    )
    from flask_sqlalchemy import SQLAlchemy
    from markupsafe import Markup
    from PIL import Image
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import inch, letter
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfgen import canvas
    from werkzeug.exceptions import RequestEntityTooLarge
    from werkzeug.middleware.proxy_fix import ProxyFix
    from werkzeug.utils import secure_filename

    # Local imports
    from models import Bag, Batch, Photo, Tray, db
    from pdf_helpers import (
        align_text,
        draw_image,
        draw_page_border,
        draw_wrapped_text,
        start_new_page
    )
    from utils import (
        format_bytes_size,
        search_bags,
        search_batches,
        water_volume_imperial,
        water_volume_metric,
        weight_imperial,
        test_db_connection,
        cosine_similarity,
        get_database_context
    )
except ImportError as e:
    print("\nMissing required package. Please run:")
    print("pip install -r requirements.txt")
    print(f"\nSpecific error: {str(e)}")
    exit(1)

# Constants
PER_PAGE = 25
UPLOAD_FOLDER = "static/uploads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
TEMP_FOLDER = "static/temp"

SUPPORTED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

# Flask app configuration
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.permanent_session_lifetime = timedelta(days=1)

# Load configuration file
config = configparser.ConfigParser()
config.read("config.ini")

# Database configuration
db_type = config.get("database", "type", fallback="sqlite")
if db_type == "mysql":
    db_config = {
        "type": config.get("database", "type", fallback="sqlite"),
        "host": config.get("database", "host", fallback="localhost"),
        "port": config.getint("database", "port", fallback=3306),
        "name": config.get("database", "name", fallback="freezedry"),
        "user": config.get("database", "user", fallback="fdtracker"),
        "password": config.get("database", "password", fallback="")
    }
    app.config["SQLALCHEMY_DATABASE_URI"] = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['name']}"
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///freezedry.db"

# Server settings
flask_host = config.get("server", "flask_host", fallback="127.0.0.1")
flask_port = config.getint("server", "flask_port", fallback=5000)
public_url = config.get("server", "public_url", fallback=None)

# Configure public URL settings if provided
if public_url:
    parsed_url = urlparse(public_url)
    app.config["SERVER_NAME"] = parsed_url.netloc

# Initialize database
db.init_app(app)
with app.app_context():
    db.create_all()

# Set up upload directory
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Add reverse proxy support
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


@app.route("/")
def root():
    if db_type == 'mysql':
        success, error = test_db_connection()
        if not success:
            return mysql_setup(error)
        try:
            return list_batches()
        except Exception as e:
            return mysql_setup(error)
    return list_batches()



@app.route("/mysql")
def mysql_setup(error=None):
    if db_type != 'mysql':
        return redirect(url_for('root'))
        
    if not error:
        success, error = test_db_connection()
        if success:
            return redirect(url_for('root'))

    setup_steps = []

    if "MySQL server not running" in str(error):
        setup_steps = [
            ("Install MySQL server:", "sudo apt install mysql-server"),
            ("Enable MySQL service:", "sudo systemctl enable mysql"),
            ("Start MySQL service:", "sudo systemctl start mysql"),
            ("Verify MySQL is running:", "sudo systemctl status mysql")
        ]
    elif "Access denied" in str(error) or "Database does not exist" in str(error):
        setup_steps = [
            ("Log into MySQL as root:", "sudo mysql"),
            ("Create the database:", f"CREATE DATABASE {db_config['name']};"),
            ("Create the user:", f"CREATE USER '{db_config['user']}'@'localhost' IDENTIFIED BY '{db_config['password']}';"),
            ("Grant privileges:", f"GRANT ALL PRIVILEGES ON {db_config['name']}.* TO '{db_config['user']}'@'localhost';"),
            ("Apply privileges:", "FLUSH PRIVILEGES;"),
            ("Exit mysql:", "exit"),
            (f"Make sure the database password for user '{db_config['user']}' matches the password in config.ini", f"ALTER USER '{db_config['user']}'@'localhost' IDENTIFIED BY '{db_config['password']}';"),
            ("Check the databse configuration in config.ini", f"[database]\ntype = mysql\nhost = {db_config['host']}\nport = {db_config['port']}\nname = {db_config['name']}\nuser = {db_config['user']}\npassword = {db_config['password']}")
        ]
    elif "Unknown database" in str(error):
        setup_steps = [
            ("Log into MySQL as root:", "sudo mysql"),
            ("Create the database:", f"CREATE DATABASE {db_config['name']};"),
            ("Grant privileges:", f"GRANT ALL PRIVILEGES ON {db_config['name']}.* TO '{db_config['user']}'@'localhost';"),
            ("Exit mysql:", "exit")
        ]
    else:
        setup_steps = [("Unknown error occurred. Please check your MySQL installation and configuration.", None)]
        
    return render_template(
        "mysql_setup.html",
        error=str(error),
        config=db_config,
        setup_steps=setup_steps
    )

@app.route("/add", methods=["GET", "POST"])
def add_batch():
    if request.method == "POST":
        tray_count = int(request.form["tray_count"])
        batch_notes = request.form.get("batch_notes", "")
        trays = []
        error = False

        # Validate tray weights and gather data
        for i in range(tray_count):
            contents = request.form.get(f"contents_{i}", "")
            starting_weight = request.form.get(f"starting_weight_{i}", "")
            tare_weight = request.form.get(f"tare_weight_{i}", 0)
            notes = request.form.get(f"notes_{i}", "")

            try:
                starting_weight = float(starting_weight)
                if starting_weight <= 0:
                    flash(f"Tray {i + 1}: Initial weight must be greater than 0.", "danger")
                    error = True
            except (ValueError, TypeError):
                flash(f"Tray {i + 1}: Initial weight must be a valid number.", "danger")
                error = True

            try:
                tare_weight = float(tare_weight)
                if starting_weight < tare_weight:
                    flash(f"Tray {i + 1}: Initial weight must be greater than empty tray weight.", "danger")
                    error = True
            except (ValueError, TypeError):
                flash(f"Tray {i + 1}: Empty tray weight must be a valid number.", "danger")
                error = True

            # Append the entered data for each tray (preserving input even if invalid)
            trays.append(
                {
                    "contents": contents,
                    "starting_weight": (
                        starting_weight if isinstance(
                            starting_weight, float) else ""
                    ),
                    "tare_weight": (
                        tare_weight if isinstance(
                            tare_weight, float) else "0"
                    ),
                    "notes": notes,
                }
            )

        if error:
            return render_template("add_batch.html", tray_count=tray_count, trays=trays, batch_notes=batch_notes)

        # Create a new batch
        batch = Batch(notes=request.form["batch_notes"])
        db.session.add(batch)

        # Add trays to the batch
        for i, tray_data in enumerate(trays):
            tray = Tray(
                contents=tray_data["contents"],
                starting_weight=tray_data["starting_weight"],
                tare_weight=tray_data["tare_weight"],
                notes=tray_data["notes"],
                position=i + 1,
            )
            batch.trays.append(tray)

        db.session.commit()
        return redirect(url_for("view_batch", id=batch.id))

    # Default state for a new form
    return render_template("add_batch.html", tray_count=1, trays=[], batch_notes="")


@app.route("/complete_batch/<int:id>", methods=["POST"])
def complete_batch(id):
    batch = db.session.get(Batch, id)
    if batch is None:
        flash(f"Batch {id} not found", "danger")
        return redirect(url_for("list_batches"))

    # Validate weights for each tray
    for tray in batch.trays:
        ending_weight = request.form.get(f"ending_weight_{tray.id}", None)
        try:
            ending_weight = float(ending_weight)
            if ending_weight <= 0:
                flash(f"Tray {tray.position}: Final weight must be greater than 0.", "danger")
                return render_template("view_batch.html", batch=batch)
            elif ending_weight > tray.starting_weight:
                flash(f"Tray {tray.position}: Final weight cannot exceed the initial weight.", "danger")
                return render_template("view_batch.html", batch=batch)
        except (ValueError, TypeError):
            flash(f"Tray {tray.position}: Final weight must be a valid number.", "danger")
            return render_template("view_batch.html", batch=batch)

    # Update tray weights and mark batch as complete
    for tray in batch.trays:
        ending_weight = float(request.form[f"ending_weight_{tray.id}"])
        tray.ending_weight = ending_weight

    batch.status = "Complete"
    batch.end_date = datetime.now(UTC)
    db.session.commit()

    return redirect(url_for("view_batch", id=batch.id))


@app.route("/delete/<int:id>", methods=["POST"])
def delete_batch(id):
    batch = db.session.get(Batch, id)
    if batch is None:
        flash(f"Batch {id} not found", "danger")
        return redirect(url_for("list_batches"))

    # Delete all photo files associated with this batch
    for photo in batch.photos:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], photo.filename)
        if os.path.exists(file_path):
            os.remove(file_path)

    db.session.delete(batch)
    db.session.commit()
    return redirect(url_for("list_batches"))


@app.route("/add_bag/<int:id>", methods=["GET", "POST"])
def add_bag(id):
    tray = db.session.get(Tray, id)
    if tray is None:
        flash(f"Tray {tray_id} not found", "danger")
        return redirect(url_for("list_batches"))

    if request.method == "POST":
        another = request.form.get("another")
        contents = request.form.get("contents", tray.contents)
        weight = request.form.get("weight", "")
        location = request.form.get("location")
        notes = request.form.get("notes")

        if tray.ending_weight is None or tray.ending_weight <= 0:
            tray.ending_weight = tray.starting_weight
        weight_loss_ratio = (
            tray.starting_weight - tray.ending_weight
        ) / (tray.ending_weight - tray.tare_weight)
        bag_weight = float(request.form["weight"])
        water_needed = weight_loss_ratio * bag_weight
        water_needed = round(water_needed, 1)

        # Query all bag IDs for the current batch
        existing_bags = db.session.query(Bag.id).filter(
            Bag.batch_id == tray.batch.id).all()
        used_numbers = []
        for bag_id, in existing_bags:
            try:
                if '-' in bag_id:
                    used_numbers.append(int(bag_id.split('-')[1]))
            except ValueError:
                continue
        next_number = max(used_numbers, default=0) + 1
        bag_id = f"{tray.batch.id:08d}-{next_number:02d}"

        bag = Bag(
            id=bag_id,
            batch_id=tray.batch.id,
            contents=request.form["contents"],
            weight=bag_weight,
            location=request.form["location"],
            notes=request.form["notes"],
            water_needed=water_needed,
        )
        db.session.add(bag)
        db.session.commit()
        flash(f"Added Bag {bag_id}", "success")

        if not another:
            return redirect(url_for("view_batch", id=bag.batch.id))

    else:
        contents = tray.contents
        weight = None
        location = None
        notes = None

    return render_template("add_bag.html", tray=tray,
                contents=contents,
                weight=weight,
                location=location,
                notes=notes)


@app.route("/delete_bag/<string:id>", methods=["POST"])
def delete_bag(id):
    bag = db.session.get(Bag, id)
    if bag is None:
        flash(f"Bag {id} not found", "danger")
        return redirect(url_for("list_batches"))
    batch_id = bag.batch.id
    db.session.delete(bag)
    db.session.commit()
    return redirect(url_for("list_batches", id=batch_id))


@app.route("/consume_bag/<string:id>", methods=["POST"])
def consume_bag(id):
    bag = db.session.get(Bag, id)
    if bag is None:
        flash(f"Bag {id} not found", "danger")
        return redirect(url_for("list_batches"))
    bag.consumed_date = datetime.now(UTC)
    db.session.commit()
    next_url = request.args.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("list_batches", id=bag.batch.id))


@app.route("/edit_batch/<int:id>", methods=["GET", "POST"])
def edit_batch(id):
    batch = db.session.get(Batch, id)
    if batch is None:
        flash(f"Batch {id} not found", "danger")
        return redirect(url_for("list_batches"))
    if request.method == "POST":
        batch.notes = request.form["batch_notes"]
        batch.start_date = datetime.strptime(
            request.form["start_date"], "%Y-%m-%d")
        end_date = request.form.get("end_date")
        batch.end_date = datetime.strptime(
            end_date, "%Y-%m-%d") if end_date else None

        # Tray Contents
        for item in batch.trays:
            item_key = f"tray-{item.id}"
            if item_key in request.form:
                item.contents = request.form[item_key]

        # Deleting trays
        trays_to_delete = request.form.getlist("delete_tray")
        for tray_id in trays_to_delete:
            tray = db.session.get(Tray, tray_id)
            db.session.delete(tray)

        # Bag Contents
        for item in batch.bags:
            item_key = f"bag-{item.id}"
            if item_key in request.form:
                item.contents = request.form[item_key]

        # Deleting bags
        bags_to_delete = request.form.getlist("delete_bag")
        for bag_id in bags_to_delete:
            bag = db.session.get(Bag, bag_id)
            db.session.delete(bag)

        # Photo Captions
        for photo in batch.photos:
            caption_key = f"caption-{photo.id}"
            if caption_key in request.form:
                photo.caption = request.form[caption_key]

        # Deleting Photos
        photos_to_delete = request.form.getlist("delete_photo")
        for photo_id in photos_to_delete:
            photo = db.session.get(Photo, photo_id)
            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"], photo.filename)
            if os.path.exists(file_path):
                os.remove(file_path)
            db.session.delete(photo)
        db.session.commit()
        return redirect(url_for("view_batch", id=batch.id))

    return render_template("edit_batch.html", batch=batch)


@app.route("/edit_tray/<int:id>", methods=["GET", "POST"])
def edit_tray(id):
    tray = db.session.get(Tray, id)
    if tray is None:
        flash(f"Tray {id} not found", "danger")
        return redirect(url_for("list_batches"))
    if request.method == "POST":
        # Handle form submission to update tray details or delete the tray
        if "delete" in request.form:
            batch_id = tray.batch.id
            db.session.delete(tray)
            db.session.commit()
            return redirect(url_for("view_batch", id=batch_id))
        else:
            tray.contents = request.form["contents"]
            tray.starting_weight = (
                float(request.form["starting_weight"])
                if request.form["starting_weight"]
                else None
            )
            tray.ending_weight = (
                float(request.form["ending_weight"])
                if request.form["ending_weight"]
                else None
            )
            tray.tare_weight = (
                float(request.form["tare_weight"])
                if request.form["tare_weight"]
                else None
            )
            tray.notes = request.form["notes"]
            db.session.commit()
            return redirect(url_for("view_batch", id=tray.batch.id))
    return render_template("edit_tray.html", tray=tray)


@app.route("/edit_bag/<string:id>", methods=["GET", "POST"])
def edit_bag(id):
    bag = db.session.get(Bag, id)
    if bag is None:
        flash(f"Bag {id} not found", "danger")
        return redirect(url_for("list_bags"))
    next_url = request.args.get("next")
    if not next_url:
        next_url = request.form["next"]
    if request.method == "POST":
        # Handle delete action
        if "delete" in request.form:
            batch_id = bag.batch.id
            db.session.delete(bag)
            db.session.commit()
            return redirect(next_url)
        else:
            # Update bag details
            bag.contents = request.form["contents"]
            bag.weight = (
                float(request.form["weight"]
                      ) if request.form["weight"] else None
            )
            bag.location = request.form["location"]
            bag.notes = request.form["notes"]
            bag.water_needed = (
                float(request.form["water_needed"])
                if request.form["water_needed"]
                else None
            )
            bag.created_date = (
                datetime.strptime(request.form["created_date"], "%Y-%m-%d")
                if request.form["created_date"]
                else None
            )
            consumed_date = request.form["consumed_date"]
            bag.consumed_date = (
                datetime.strptime(
                    consumed_date, "%Y-%m-%d") if consumed_date else None
            )

            db.session.commit()
        if next_url:
            return redirect(next_url)
    return render_template("edit_bag.html", bag=bag, next=next_url)


@app.route("/update_weight/<int:id>", methods=["POST"])
def update_weight(id):
    batch = db.session.get(Batch, id)
    if batch is None:
        flash(f"Batch {id} not found", "danger")
        return redirect(url_for("list_batches"))

    for tray in batch.trays:
        tray_id = str(tray.id)
        # Retrieve the weight entered by the user from the form
        entered_weight = request.form.get(f"ending_weight_{tray_id}")
        if entered_weight:
            # Save the entered weight as the tray's previous weight
            tray.previous_weight = float(entered_weight)

    # Commit the changes to the database
    db.session.commit()

    # Redirect back to the batch details page with the batch expanded
    return redirect(url_for("view_batch", id=id))


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        directory="static", path="freezedryer.svg", mimetype="image/svg+xml"
    )


@app.route("/add_photo/<int:id>", methods=["GET", "POST"])
def add_photo(id):
    batch = db.session.get(Batch, id)
    if batch is None:
        flash(f"Batch {id} not found", "danger")
        return redirect(url_for("list_batches"))

    if request.method == "POST":
        if "photo" not in request.files:
            flash("No file part", "danger")
            return render_template("add_photo.html", batch=batch)

        file = request.files["photo"]
        caption = request.form.get("caption", "")

        if file.filename == "":
            flash("No selected file", "danger")
            return render_template("add_photo.html", batch=batch)

        # Save the file temporarily for validation
        original_name = secure_filename(file.filename)
        temp_path = os.path.join(TEMP_FOLDER, original_name)
        os.makedirs(TEMP_FOLDER, exist_ok=True)
        file.save(temp_path)

        try:
            # Check file size
            if os.path.getsize(temp_path) > MAX_FILE_SIZE:
                os.remove(temp_path)
                flash("File is too large (max 50MB)", "danger")
                return render_template("add_photo.html", batch=batch)

            # Validate file type using MIME type
            mime = magic.Magic(mime=True)
            mime_type = mime.from_file(temp_path)
            if mime_type not in SUPPORTED_MIME_TYPES:
                os.remove(temp_path)
                flash("Unsupported file type", "danger")
                return render_template("add_photo.html", batch=batch)

            # Create a Photo entry in the database to get the photo ID
            photo = Photo(batch_id=batch.id, filename="temp", caption=caption)
            db.session.add(photo)
            db.session.flush()  # Flush to generate the photo.id without committing

            # Use the photo ID to generate the filename
            final_filename = f"IMG_{photo.id}.webp"
            final_path = os.path.join(UPLOAD_FOLDER, final_filename)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)

            # Attempt to resize and convert to WebP
            with Image.open(temp_path) as img:
                # Convert HEIC/HEIF to a supported format
                if mime_type in {"image/heic", "image/heif"}:
                    img = img.convert("RGB")

                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.thumbnail((800, 600))
                img.save(final_path, "WEBP")

            # Clean up the temporary file
            os.remove(temp_path)

            # Update the Photo entry with the final filename and commit
            photo.filename = final_filename
            db.session.commit()

            return redirect(url_for("view_batch", id=id))

        except Exception as e:
            # Handle any errors
            os.remove(temp_path)
            current_app.logger.error(f"Error processing file upload: {e}")
            return render_template("add_photo.html", batch=batch, error="An error occurred while processing the file")

    return render_template("add_photo.html", batch=batch)


@app.route("/print_label/<string:id>")
def print_label(id):
    # First try to find a bag with this ID
    bag = db.session.get(Bag, id)
    if bag:
        bags = [bag]
    else:
        # If no bag found, try to parse as batch ID
        try:
            batch_id = int(id)
            batch = db.session.get(Batch, batch_id)
            if batch is None:
                flash(f"Batch {id} not found", "danger")
                return redirect(request.referrer or url_for("list_batches"))
            bags = batch.bags
            if not bags:
                flash(f"No bags found in batch {id}", "warning")
                return redirect(request.referrer or url_for("view_batch", id=batch_id))
        except ValueError:
            flash(f"Bag {id} not found", "danger")
            return redirect(request.referrer or url_for("list_bags"))

    # Create PDF with multiple pages
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(4 * inch, 6 * inch))

    for bag in bags:
        # Create QR code with batch URL
        batch_url = url_for("view_bag", id=bag.id, _external=True)
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(batch_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Save QR code to memory
        qr_buffer = BytesIO()
        qr_img.save(qr_buffer)
        qr_buffer.seek(0)

        # Draw the border and line
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.02 * inch)
        c.roundRect(0.1 * inch, 0.1 * inch, 3.8 * inch, 5.8 * inch, 0.25 * inch)
        c.line(0.1 * inch, 5.4 * inch, 3.9 * inch, 5.4 * inch)

        date_text = bag.batch.start_date.strftime("%Y-%m-%d")
        align_text(
            c,
            f"Batch: {bag.batch.id:08d}",
            y=5.55 * inch,
            margin=0.3 * inch,
            font_name="Helvetica-Bold",
            font_size=14,
        )
        align_text(
            c,
            date_text,
            "right",
            y=5.55 * inch,
            margin=0.3 * inch,
            font_name="Helvetica-Bold",
            font_size=14,
            page_width=4,
        )

        # Add centered contents with text wrapping
        c.setFont("Helvetica-Bold", 14)
        text_width = 3.6 * inch
        wrapped_lines = simpleSplit(bag.contents, c._fontname, c._fontsize, text_width)
        y = 5.0 * inch
        for line in wrapped_lines:
            text_length = c.stringWidth(line)
            x = (4 * inch - text_length) / 2
            c.drawString(x, y, line)
            y -= 20

        # Add details below contents
        y -= 10
        c.setFont("Helvetica", 12)
        x = 0.2 * inch

        # Fixed details
        y = align_text(c, f"Bag ID: {bag.id}", y=y, margin=x)
        y = align_text(c, f"Freeze Dried Weight: {bag.weight}g ({weight_imperial(bag.weight)})", y=y, margin=x)
        original_weight = round(bag.weight + bag.water_needed, 1)
        y = align_text(c, f"Original Weight: ~{original_weight}g ({weight_imperial(original_weight)})", y=y, margin=x)
        w = bag.water_needed
        water_needed = f"{water_volume_metric(w)} ({water_volume_imperial(w)})"
        y = align_text(c, f"Water Needed: ~{water_needed}", y=y, margin=x)

        # Wrapping text for location
        if bag.location:
            wrapped_location = simpleSplit(
                f"Location: {bag.location}", c._fontname, c._fontsize, text_width
            )
            for line in wrapped_location:
                c.drawString(x, y, line)
                y -= 15

        # Wrapping text for notes
        if bag.notes:
            wrapped_notes = simpleSplit(
                f"Notes: {bag.notes}", c._fontname, c._fontsize, text_width
            )
            for line in wrapped_notes:
                c.drawString(x, y, line)
                y -= 15

        # Add QR code at bottom
        qr_width = qr_height = 1 * inch
        qr_x = (4 * inch - qr_width) / 2
        qr_y = 0.2 * inch
        c.drawImage(ImageReader(qr_buffer), qr_x, qr_y, qr_width, qr_height)
        y = align_text(
            c, f"{batch_url}", "center", y=0.175 * inch, font_size=8, page_width=4
        )

        c.showPage()

    c.save()
    buffer.seek(0)

    return send_file(
        buffer, 
        download_name=f"labels_{id}.pdf", 
        mimetype="application/pdf"
    )


@app.route("/backup")
def create_backup():
        return send_file(
        create_backup_file("Backup requested by user"),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f'fdtracker_backup_{datetime.now().strftime("%Y%m%d")}.zip',)

def create_backup_file(comment=""):
    manifest = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "comment": comment,
        "files": [],
    }
    backup_hasher = hashlib.sha256()
    
    # Export database tables to JSON
    db_data = {
        "batches": [],
        "trays": [],
        "bags": [],
        "photos": []
    }
    
    # Export all batches and their relationships
    batches = db.session.query(Batch).all()
    for batch in batches:
        batch_data = {
            "id": batch.id,
            "start_date": batch.start_date.isoformat(),
            "end_date": batch.end_date.isoformat() if batch.end_date else None,
            "notes": batch.notes,
            "status": batch.status
        }
        db_data["batches"].append(batch_data)
    
    # Export all trays
    trays = db.session.query(Tray).all()
    for tray in trays:
        tray_data = {
            "id": tray.id,
            "batch_id": tray.batch_id,
            "contents": tray.contents,
            "starting_weight": tray.starting_weight,
            "ending_weight": tray.ending_weight,
            "previous_weight": tray.previous_weight,
            "tare_weight": tray.tare_weight,
            "notes": tray.notes,
            "position": tray.position
        }
        db_data["trays"].append(tray_data)
    
    # Export all bags
    bags = db.session.query(Bag).all()
    for bag in bags:
        bag_data = {
            "id": bag.id,
            "batch_id": bag.batch_id,
            "contents": bag.contents,
            "weight": bag.weight,
            "location": bag.location,
            "notes": bag.notes,
            "water_needed": bag.water_needed,
            "created_date": bag.created_date.isoformat(),
            "consumed_date": bag.consumed_date.isoformat() if bag.consumed_date else None
        }
        db_data["bags"].append(bag_data)
    
    # Export all photos
    photos = db.session.query(Photo).all()
    for photo in photos:
        photo_data = {
            "id": photo.id,
            "batch_id": photo.batch_id,
            "filename": photo.filename,
            "caption": photo.caption
        }
        db_data["photos"].append(photo_data)

    backup = BytesIO()
    with ZipFile(backup, "w") as zip_file:
        # Add database JSON to zip
        db_json = json.dumps(db_data, indent=4)
        backup_hasher.update(db_json.encode())
        manifest["files"].append({
            "name": "database.json",
            "hash": hashlib.sha256(db_json.encode()).hexdigest()
        })
        zip_file.writestr("database.json", db_json)
        
        # Add photos to zip
        for photo in photos:
            file_path = os.path.join(UPLOAD_FOLDER, photo.filename)
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    file_data = f.read()
                    backup_hasher.update(file_data)
                    manifest["files"].append({
                        "name": photo.filename,
                        "hash": hashlib.sha256(file_data).hexdigest()
                    })
                    zip_file.writestr(photo.filename, file_data)
        
        manifest["hash"] = backup_hasher.hexdigest()
        zip_file.writestr("manifest.json", json.dumps(manifest, indent=4))

    backup.seek(0)
    return backup


@app.route("/restore", methods=["GET", "POST"])
def restore_backup(snapshot=None):
    if snapshot:
        template = "snapshots.html"
        backup = snapshot
    else:
        template = "restore_backup.html"
        if request.method == "GET":
            return render_template(template)

        if "backup_file" not in request.files:
            flash("No backup file provided", "danger")
            return render_template(template)

        backup = request.files["backup_file"]

        if backup.filename == "":
            flash("No backup file selected", "danger")
            return render_template(template)

    try:
        with ZipFile(backup, "r") as zip_file:
            found_files = set(name for name in zip_file.namelist())
            backup_hasher = hashlib.sha256()

            if "manifest.json" not in found_files:
                flash("Invalid backup file: no manifest", "danger")
                return render_template(template)

            if "database.json" not in found_files:
                flash("Invalid backup file: no database export", "danger")
                return render_template(template)

            manifest = json.loads(zip_file.read("manifest.json"))
            manifest_hashes = {file_entry["name"]: file_entry["hash"] for file_entry in manifest["files"]}
            expected_files = {file_entry["name"] for file_entry in manifest["files"]} | {"manifest.json"}
            missing_files = expected_files - found_files
            extra_files = found_files - expected_files

            if missing_files:
                flash(f"Missing files in backup: {', '.join(missing_files)}", "danger")
                return render_template(template)

            if extra_files:
                flash(f"Extra files in backup: {', '.join(extra_files)}", "danger")
                return render_template(template)

            # Verify database.json structure
            db_data = json.loads(zip_file.read("database.json"))
            required_tables = {"batches", "trays", "bags", "photos"}
            if not all(table in db_data for table in required_tables):
                flash("Invalid database export: missing required tables", "danger")
                return render_template(template)

            # Verify hashes
            for filename, manifest_hash in manifest_hashes.items():
                file_data = zip_file.read(filename)
                backup_hasher.update(file_data)
                actual_hash = hashlib.sha256(file_data).hexdigest()
                if actual_hash != manifest_hash:
                    flash(f"Hash mismatch for file {filename}", "danger")
                    return render_template(template)
                if filename != "database.json" and not filename.startswith("manifest"):
                    mime = magic.from_buffer(file_data, mime=True)
                    if not mime.startswith("image/"):
                        flash(f"Invalid file type: {mime}", "danger")
                        return render_template(template)

            backup_hash = backup_hasher.hexdigest()
            if backup_hash != manifest["hash"]:
                flash("Invalid backup file: Hash mismatch!", "danger")
                return render_template(template)

            # Create snapshot before restoration
            backup_dir = os.path.join("static", "snapshots")
            os.makedirs(backup_dir, exist_ok=True)
            snapshot = create_backup_file("Snapshot before restore")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"snapshot_{timestamp}.zip")
            with open(backup_path, 'wb') as f:
                f.write(snapshot.getvalue())
            flash("Snapshot created", "info")

            # Clear database tables
            db.session.query(Photo).delete()
            db.session.query(Bag).delete()
            db.session.query(Tray).delete()
            db.session.query(Batch).delete()
            db.session.commit()

            # Clear uploads directory
            if os.path.exists(UPLOAD_FOLDER):
                shutil.rmtree(UPLOAD_FOLDER)
            os.makedirs(UPLOAD_FOLDER)

            # Restore database from JSON
            for batch_data in db_data["batches"]:
                batch = Batch(
                    id=batch_data["id"],
                    start_date=datetime.fromisoformat(batch_data["start_date"]),
                    end_date=datetime.fromisoformat(batch_data["end_date"]) if batch_data["end_date"] else None,
                    notes=batch_data["notes"],
                    status=batch_data["status"]
                )
                db.session.add(batch)

            for tray_data in db_data["trays"]:
                tray = Tray(
                    id=tray_data["id"],
                    batch_id=tray_data["batch_id"],
                    contents=tray_data["contents"],
                    starting_weight=tray_data["starting_weight"],
                    ending_weight=tray_data["ending_weight"],
                    previous_weight=tray_data["previous_weight"],
                    tare_weight=tray_data["tare_weight"],
                    notes=tray_data["notes"],
                    position=tray_data["position"]
                )
                db.session.add(tray)

            for bag_data in db_data["bags"]:
                bag = Bag(
                    id=bag_data["id"],
                    batch_id=bag_data["batch_id"],
                    contents=bag_data["contents"],
                    weight=bag_data["weight"],
                    location=bag_data["location"],
                    notes=bag_data["notes"],
                    water_needed=bag_data["water_needed"],
                    created_date=datetime.fromisoformat(bag_data["created_date"]),
                    consumed_date=datetime.fromisoformat(bag_data["consumed_date"]) if bag_data["consumed_date"] else None
                )
                db.session.add(bag)

            for photo_data in db_data["photos"]:
                photo = Photo(
                    id=photo_data["id"],
                    batch_id=photo_data["batch_id"],
                    filename=photo_data["filename"],
                    caption=photo_data["caption"]
                )
                db.session.add(photo)

            db.session.commit()

            # Extract photos
            for filename in zip_file.namelist():
                if filename not in {"manifest.json", "database.json"}:
                    with zip_file.open(filename) as source, open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as target:
                        shutil.copyfileobj(source, target)

            flash("Backup restored successfully!", "success")

    except Exception as e:
        flash(f"Invalid backup file: {e.__class__.__name__}: {str(e)}", "danger")
        return render_template(template)

    return redirect(url_for("list_batches"))


@app.route("/snapshots", methods=["GET", "POST"])
def manage_snapshots():
    snapshot_dir = os.path.join("static", "snapshots")
    os.makedirs(snapshot_dir, exist_ok=True)
    
    if request.method == "POST":
        if "create_snapshot" in request.form:
            # Create new snapshot
            comment = request.form.get("comment")
            snapshot = create_backup_file(comment)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshot_path = os.path.join(snapshot_dir, f"snapshot_{timestamp}.zip")
            with open(snapshot_path, 'wb') as f:
                f.write(snapshot.getvalue())
            flash("New snapshot created successfully", "success")
            
        elif "delete_snapshot" in request.form:
            # Delete selected snapshot
            filename = request.form.get("filename")
            if filename:
                file_path = os.path.join(snapshot_dir, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    flash(f"Snapshot {filename} deleted successfully", "success")
                    
        elif "restore_snapshot" in request.form:
            filename = request.form.get("filename")
            if filename:
                file_path = os.path.join(snapshot_dir, filename)
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as f:
                        restore_backup(file_path)
    
    # Get list of snapshots with creation times
    snapshots = []
    for filename in os.listdir(snapshot_dir):
        if filename.endswith('.zip'):
            created = None
            comment = ""
            try:
                with ZipFile(os.path.join(snapshot_dir,filename), "r") as zip_file:
                    if "manifest.json" in zip_file.namelist():
                        with zip_file.open("manifest.json") as manifest_file:
                            manifest = json.load(manifest_file)
                            created = manifest["timestamp"].split(".")[0]
                            comment = manifest.get("comment", "")

            except Exception as e:
                        flash(f"Error loading manifest.json: {e}", "danger")


            if created:            
                path = os.path.join(snapshot_dir, filename)
                size = format_bytes_size(os.path.getsize(path))
                snapshots.append({
                    'filename': filename,
                    'created': created,
                    'comment': comment,
                    'size': size
                })
    
    # Sort snapshots by creation time, newest first
    snapshots.sort(key=lambda x: x['created'], reverse=True)
    total, used, free = shutil.disk_usage(snapshot_dir)
    free_space = format_bytes_size(free)
    
    return render_template("snapshots.html", snapshots=snapshots, free_space=free_space)


@app.route("/list_batches", methods=["GET", "POST"])
def list_batches():
    page = int(request.form.get("page", 1))
    search_query = request.cookies.get(
        "batch_search", request.form.get("search", "")).strip()
    date_from = request.cookies.get(
        "batch_date_from", request.form.get("date_from"))
    date_to = request.cookies.get("batch_date_to", request.form.get("date_to"))
    id = request.args.get("id", type=int)

    query = search_batches(search_query, date_from, date_to)
    query = query.order_by(Batch.id.desc())
    batch_count = query.count()

    # Find the page containing the specified batch id
    if id is not None:
        batches = query.order_by(Batch.id.desc()).all()
        batch_ids = [batch.id for batch in batches]
        if id in batch_ids:
            batch_index = batch_ids.index(id)
            page = (batch_index // PER_PAGE) + 1

    pagination = query.paginate(page=page, per_page=PER_PAGE)
    batches = pagination.items

    return render_template(
        "list_batches.html",
        batches=batches,
        batch_count=batch_count,
        pagination=pagination,
        search_query=search_query,
        date_from=date_from,
        date_to=date_to,
    )


@app.route("/list_bags", methods=["GET", "POST"])
def list_bags():
    id = request.args.get("id")
    page = int(request.form.get("page", 1))
    search_query = request.cookies.get(
        "bag_search", request.form.get("search", "")).strip()
    date_from = request.cookies.get(
        "bag_date_from", request.form.get("date_from"))
    date_to = request.cookies.get("bag_date_to", request.form.get("date_to"))

    # Handle checkbox states
    newest_form = request.form.get("newest")
    newest_cookie = request.cookies.get("bag_newest")
    newest = newest_form == "on" if newest_form is not None else newest_cookie == "true"
    if newest_form is None and newest_cookie is None:
        newest = True  # Default value for fresh visits

    unopened_form = request.form.get("unopened")
    unopened_cookie = request.cookies.get("bag_unopened")
    unopened = unopened_form == "on" if unopened_form is not None else unopened_cookie == "true"
    if unopened_form is None and unopened_cookie is None:
        unopened = False  # Default value for fresh visits

    # Base query
    query = search_bags(search_query, date_from, date_to, unopened)

    # Find the page containing the specified bag id
    if id is not None:
        bags = query.order_by(Bag.id.desc()).all()
        bag_ids = [bag.id for bag in bags]
        if id in bag_ids:
            bag_index = bag_ids.index(id)
            page = (bag_index // PER_PAGE) + 1

    if newest:
        query = query.order_by(Bag.id.desc())
    else:
        query = query.order_by(Bag.id.asc())

    query = query.order_by(Bag.created_date.desc())
    bag_count = query.count()

    # Paginate the filtered results
    pagination = query.paginate(page=page, per_page=PER_PAGE)
    bags = pagination.items

    return render_template(
        "list_bags.html",
        bags=bags,
        bag_count=bag_count,
        pagination=pagination,
        search_query=search_query,
        date_from=date_from,
        date_to=date_to,
        unopened=unopened,
        newest=newest,
    )


@app.route("/view_batch/<int:id>", methods=["GET"])
def view_batch(id):
    batch = db.session.get(Batch, id)
    if batch is None:
        flash(f"Batch {id} not found", "danger")
        return redirect(url_for("list_batches"))
    search_query = request.args.get("search_query", "")
    return render_template("view_batch.html", batch=batch, search_query=search_query)


@app.route("/view_bag/<string:id>", methods=["GET"])
def view_bag(id):
    bag = db.session.get(Bag, id)
    if bag is None:
        flash(f"Bag {id} not found", "danger")
        return redirect(url_for("list_bags"))
    search_query = request.args.get("search_query", "")
    return render_template("view_bag.html", bag=bag, search_query=search_query)


@app.template_filter("highlight")
def highlight_search(text, search):
    if not search:
        return text
    escaped_search = re.escape(search)
    return Markup(
        re.sub(f"({escaped_search})", r"<mark>\1</mark>",
               text, flags=re.IGNORECASE)
    )


@app.route("/batch_report/")
@app.route("/batch_report/<int:id>")
def batch_report(id=None):
    if id:
        # Single batch report
        batch = db.session.get(Batch, id)
        if batch is None:
            flash(f"Batch {id} not found", "danger")
            return redirect(url_for("list_batches"))
        buffer = create_batch_pdf(batch=batch)
    else:
        # Multiple batch report from search parameters
        search_query = request.cookies.get("batch_search", "").strip()
        date_from = request.cookies.get("batch_date_from")
        date_to = request.cookies.get("batch_date_to")

        # Use existing search_batches function to get filtered batches
        query = search_batches(search_query, date_from, date_to)
        batches = query.order_by(Batch.id).all()
        buffer = create_batch_pdf(batches=batches)

    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"batch_report_{timestamp}.pdf"

    return send_file(buffer, mimetype="application/pdf", download_name=filename)


def create_batch_pdf(batch=None, batches=[]):
    if batch is not None:
        # Load fresh batch instance with all relationships
        batch = (
            db.session.query(Batch)
            .options(
                db.joinedload(Batch.trays),
                db.joinedload(Batch.bags),
                db.joinedload(Batch.photos),
            )
            .filter_by(id=batch.id)
            .first()
        )
        batches = [batch]
    else:
        # Load all batches with relationships
        batches = (
            db.session.query(Batch)
            .options(
                db.joinedload(Batch.trays),
                db.joinedload(Batch.bags),
                db.joinedload(Batch.photos),
            )
            .filter(Batch.id.in_([b.id for b in batches]))
            .all()
        )

    buffer = BytesIO()
    doc = canvas.Canvas(buffer, pagesize=letter)

    # Letter dimensions and margins
    page_width, page_height = letter
    margin = 0.5 * inch  # Half inch margin
    border_padding = 0.1 * inch

    # Content area inside border
    content_width = page_width - (2 * margin)
    content_height = page_height - (2 * margin)

    for batch in batches:
        # Start Batch Details Page
        page_title = f"Batch {batch.id:08d}"
        y = start_new_page(doc, title=page_title)

        # Batch Details
        y = align_text(
            doc,
            "Batch Information",
            y=y,
            margin=margin + 20,
            font_name="Helvetica-Bold",
            font_size=14,
        )
        align_text(doc, "Start Date:", y=y, margin=margin + 20)
        y = align_text(
            doc, f"{batch.start_date.strftime('%Y-%m-%d')}", y=y, margin=margin + 100
        )
        end_date_text = (f"{batch.end_date.strftime('%Y-%m-%d')}" if batch.end_date else "N / A")
        align_text(doc, "End Date:", y=y, margin=margin + 20)
        y = align_text(doc, f"{end_date_text}", y=y, margin=margin + 100)
        align_text(doc, "Status:", y=y, margin=margin + 20)
        y = align_text(doc, f"{batch.status}", y=y, margin=margin + 100)
        y -= 20
        y = align_text(
            doc,
            "Notes",
            y=y,
            margin=margin + 20,
            font_name="Helvetica-Bold",
            font_size=14,
        )
        notes_text = f"{batch.notes.strip()}" if batch.notes else "N/A"
        y = draw_wrapped_text(
            doc, notes_text, margin + 20, y, new_page_title=page_title
        )
        y -= 20

        # Trays Section
        y = align_text(
            doc,
            "Trays",
            y=y,
            margin=margin + 20,
            font_name="Helvetica-Bold",
            font_size=14,
        )

        for tray in batch.trays:
            if y < (margin + 105):  # Check if we need a new page
                doc.showPage()
                y = start_new_page(doc, title=page_title)

            y = align_text(
                doc,
                f"Tray {tray.position}",
                y=y,
                margin=margin + 40,
                font_name="Helvetica-Bold",
                font_size=12,
            )
            align_text(doc, "Contents:", y=y, margin=margin + 60)
            y = align_text(doc, f"{tray.contents}", y=y, margin=margin + 160)
            starting_weight = tray.starting_weight - tray.tare_weight
            align_text(doc, "Starting Weight:", y=y, margin=margin + 60)
            y = align_text(doc, f"{starting_weight}g ({weight_imperial(starting_weight)})",
                           y=y, margin=margin + 160)
            if tray.ending_weight is not None:
                ending_weight = tray.ending_weight - tray.tare_weight
                align_text(doc, "Ending Weight:", y=y, margin=margin + 60)
                y = align_text(doc, f"{ending_weight}g ({weight_imperial(ending_weight)})",
                            y=y, margin=margin + 160)
                w = tray.starting_weight - tray.ending_weight
                water_removed = f"{water_volume_metric(w)} ({water_volume_imperial(w)})"
                align_text(doc, "Water Removed:", y=y, margin=margin + 60)
                y = align_text(doc, f"{water_removed}",
                               y=y, margin=margin + 160)
            else:
                align_text(doc, "Water Removed:", y=y, margin=margin + 60)
                y = align_text(doc, "Not yet measured",
                               y=y, margin=margin + 160)
            if tray.notes:
                y -= 5
                y = draw_wrapped_text(
                    doc,
                    f"Notes: {tray.notes}",
                    margin + 60,
                    y,
                    new_page_title=page_title,
                )
            y -= 10

        # Bags Section
        if batch.bags:
            if y < (margin + 110):
                doc.showPage()
                y = start_new_page(doc, title=page_title)

            y = align_text(
                doc,
                "Bags",
                y=y,
                margin=margin + 20,
                font_name="Helvetica-Bold",
                font_size=14,
            )

            for bag in batch.bags:
                if y < (margin + 100):
                    doc.showPage()
                    y = start_new_page(doc, title=page_title)

                y = align_text(
                    doc,
                    f"Bag {bag.id}",
                    y=y,
                    margin=margin + 40,
                    font_name="Helvetica-Bold",
                    font_size=12,
                )
                align_text(doc, "Created:", y=y, margin=margin + 60)
                y = align_text(doc, f"{bag.created_date.strftime('%Y-%m-%d')}",
                               y=y, margin=margin + 150)
                align_text(doc, "Consumed:", y=y, margin=margin + 60)
                if bag.consumed_date is not None:
                    y = align_text(doc, f"{bag.consumed_date.strftime('%Y-%m-%d')}",
                                   y=y, margin=margin + 150)
                else:
                    y = align_text(doc, "Not yet consumed",
                                   y=y, margin=margin + 150)
                align_text(doc, "Contents:", y=y, margin=margin + 60)
                y = align_text(doc, f"{bag.contents}",
                               y=y, margin=margin + 150)
                align_text(doc, "Location:", y=y, margin=margin + 60)
                y = align_text(doc, f"{bag.location}",
                               y=y, margin=margin + 150)
                align_text(doc, "Weight:", y=y, margin=margin + 60)
                y = align_text(doc, f"{bag.weight}g ({weight_imperial(bag.weight)})", y=y, margin=margin + 150)
                align_text(doc, "Water Needed:", y=y, margin=margin + 60)
                w = bag.water_needed
                water_needed = f"{water_volume_metric(w)} ({water_volume_imperial(w)})"
                y = align_text(
                    doc, f"about {water_needed}", y=y, margin=margin + 150)
                if bag.notes:
                    y -= 5
                    y = draw_wrapped_text(
                        doc,
                        f"Notes: {bag.notes}",
                        margin + 60,
                        y,
                        new_page_title=page_title,
                    )
                y -= 10

        # Photos Section
        if batch.photos:
            first = True

            for photo in batch.photos:
                img_path = os.path.join(
                    app.config["UPLOAD_FOLDER"], photo.filename)
                if os.path.exists(img_path):
                    img = Image.open(img_path)
                    aspect = img.width / img.height
                    if aspect < 1:  # Tall image
                        # Cap height at 500 points
                        height = min(img.height, 500)
                        width = height * aspect
                    else:  # Wide or square image
                        width = 400
                        height = width / aspect

                    if y < (margin + height):  # Check if we need a new page
                        doc.showPage()
                        y = start_new_page(doc, title=page_title)

                    if first:
                        y = align_text(
                            doc,
                            "Photos",
                            y=y,
                            margin=margin + 20,
                            font_name="Helvetica-Bold",
                            font_size=14,
                        )
                        first = False

                    # Calculate x position to center the image
                    x = (page_width - width) / 2

                    doc.drawImage(img_path, x, y - height,
                                  width=width, height=height)
                    y -= height

                    if photo.caption:
                        y -= 15
                        for line in simpleSplit(photo.caption, "Helvetica", 14, 5 * inch):
                            y = align_text(
                                doc,
                                line,
                                "center",
                                y=y,
                                font_size=14,
                            )
                    y -= 10
        doc.showPage()
    doc.save()
    return buffer


@app.route("/bag_location_inventory")
def bag_location_inventory():
    # Get search parameters from cookies
    search_query = request.cookies.get("bag_search", "").strip()
    date_from = request.cookies.get("bag_date_from")
    date_to = request.cookies.get("bag_date_to")

    # Use existing search_bags function to get filtered bags
    query = search_bags(search_query, date_from, date_to, unopened=True)
    bags = query.options(db.joinedload(Bag.batch)).order_by(
        Bag.location, Bag.id).all()

    buffer = create_bag_location_inventory_pdf(bags)
    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"location_inventory_{timestamp}.pdf"

    return send_file(buffer, mimetype="application/pdf", download_name=filename)


def create_bag_location_inventory_pdf(bags):
    buffer = BytesIO()
    doc = canvas.Canvas(buffer, pagesize=letter)
    margin = 0.5 * inch

    # Group bags by location
    location_groups = {}
    for bag in bags:
        location = bag.location or "Unspecified Location"
        if location not in location_groups:
            location_groups[location] = []
        location_groups[location].append(bag)

    # Start first page
    y = start_new_page(doc, title="Location Inventory")

    # Print inventory by location
    for location in sorted(location_groups.keys()):
        if y < (margin + 60):
            doc.showPage()
            y = start_new_page(doc, title="Location Inventory")

        # Location header
        y = align_text(
            doc,
            location,
            y=y,
            margin=margin + 20,
            font_name="Helvetica-Bold",
            font_size=14,
        )
        y -= 10

        # Print bags in this location
        for bag in location_groups[location]:
            if y < (margin + 60):
                doc.showPage()
                y = start_new_page(doc, title="Location Inventory")

            # Bag details
            align_text(
                doc,
                f"Bag {bag.id}",
                y=y,
                margin=margin + 40,
                font_name="Helvetica-Bold",
            )
            align_text(
                doc,
                f"Created: {bag.created_date.strftime('%Y-%m-%d')}",
                y=y,
                margin=margin + 200,
            )
            y = align_text(doc, f"Weight: {bag.weight}g ({weight_imperial(bag.weight)})", y=y, margin=margin + 350)
            y = draw_wrapped_text(
                doc, f"Contents: {bag.contents}", margin + 40, y)
            if bag.notes:
                y = draw_wrapped_text(
                    doc, f"Notes: {bag.notes}", margin + 40, y)
            y -= 10

        y -= 10  # Extra space between locations

    doc.save()
    return buffer


@app.route("/bag_inventory")
def bag_inventory():
    # Get all search parameters from cookies
    search_query = request.cookies.get("bag_search", "").strip()
    date_from = request.cookies.get("bag_date_from")
    date_to = request.cookies.get("bag_date_to")
    unopened = request.cookies.get("bag_unopened") == "true"
    newest = request.cookies.get("bag_newest") == "true"

    # Use existing search_bags function to get filtered bags
    query = search_bags(search_query, date_from, date_to, unopened)

    # Apply ordering based on newest parameter
    if newest:
        query = query.order_by(Bag.id.desc())
    else:
        query = query.order_by(Bag.id.asc())

    # Add secondary ordering by location
    query = query.options(db.joinedload(Bag.batch)).order_by(Bag.location)
    bags = query.all()

    buffer = create_bag_inventory_pdf(bags)
    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bag_inventory_{timestamp}.pdf"

    return send_file(buffer, mimetype="application/pdf", download_name=filename)


def create_bag_inventory_pdf(bags):
    buffer = BytesIO()
    doc = canvas.Canvas(buffer, pagesize=letter)
    margin = 0.5 * inch

    # Start first page
    y = start_new_page(doc, title="Bag Inventory")

    # Print each bag's details
    for bag in bags:
        if y < (margin + 80):
            doc.showPage()
            y = start_new_page(doc, title="Bag Inventory")

        # Bag header with ID and status
        status = "CONSUMED" if bag.consumed_date else "AVAILABLE"
        header = f"Bag {bag.id} - {status}"
        y = align_text(
            doc,
            header,
            y=y,
            margin=margin + 20,
            font_name="Helvetica-Bold",
            font_size=12,
        )

        # Bag details
        y = draw_wrapped_text(doc, f"Contents: {bag.contents}", margin + 40, y)
        y = draw_wrapped_text(
            doc, f"Location: {bag.location or 'Unspecified'}", margin + 40, y
        )
        y = draw_wrapped_text(doc, f"Weight: {bag.weight}g ({weight_imperial(bag.weight)})", margin + 40, y)

        if bag.water_needed:
            w = bag.water_needed
            water_needed = f"{water_volume_metric(w)} ({water_volume_imperial(w)})"
            y = draw_wrapped_text(
                doc, f"Water Needed: about {water_needed}", margin + 40, y
            )

        if bag.notes:
            y = draw_wrapped_text(doc, f"Notes: {bag.notes}", margin + 40, y)

        if bag.consumed_date:
            consumed_date = bag.consumed_date.strftime("%Y-%m-%d")
            y = draw_wrapped_text(
                doc, f"Consumed: {consumed_date}", margin + 40, y)

        y -= 20  # Extra space between bags

    doc.save()
    return buffer

def update_schema():
    with app.app_context():
        # Check if tare_weight column exists in tray table
        inspector = db.inspect(db.engine)
        has_tare_weight = 'tare_weight' in [col['name'] for col in inspector.get_columns('tray')]
        
        if not has_tare_weight:
            # Add the column
            with db.engine.connect() as conn:
                conn.execute(db.text('ALTER TABLE tray ADD COLUMN tare_weight FLOAT'))
                # Set default value of 0 for all existing trays
                conn.execute(db.text('UPDATE tray SET tare_weight = 0'))
                conn.commit()

        # Check if uploaded_at column exists in photo table
        has_uploaded_at = 'uploaded_at' in [col['name'] for col in inspector.get_columns('photo')]
        
        if has_uploaded_at:
            # Remove the column
            with db.engine.connect() as conn:
                conn.execute(db.text('ALTER TABLE photo DROP COLUMN uploaded_at'))
                conn.commit()

@app.route("/ai", methods=["GET", "POST"])
def ai_chat():
    session.permanent = True
    openai_key = config.get("openai", "api_key", fallback=None)
    if not openai_key:
        flash("OpenAI API key not configured", "danger")
        return redirect(request.referrer or url_for("root"))

    client = OpenAI(api_key=openai_key)

    # Clear chat history if coming from a different page
    if request.referrer and url_for('ai_chat') not in request.referrer:
        session.pop("chat_history", None)
    
    referrer = request.form.get("referrer") or request.referrer or url_for("root")
    if url_for("ai_chat") in referrer:
        referrer = url_for("root")
    
    if "chat_history" not in session:
        session["chat_history"] = []
    
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        if question:
            context = get_database_context(question, client)

            messages = [
                {"role": "system", "content": "You are a helpful assistant for a freeze-drying application. Focus on the current question using this database context: " + context},
                *session.get("chat_history", []),
                {"role": "user", "content": question}
            ]
            

            try:
                response = client.chat.completions.create(
                    model = config.get("openai", "model", fallback="gpt-5-turbo"),
                    messages=messages
                )
                
                answer = response.choices[0].message.content
                session["chat_history"].extend([
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer}
                ])
                
            except Exception as e:
                flash(f"Error getting AI response: {str(e)}", "danger")
                
    return render_template(
        "ai_chat.html",
        chat_history=session.get("chat_history", []),
        referrer=referrer
    )

@app.context_processor
def utility_processor():
    return {
        'openai_enabled': config.getboolean('openai', 'enabled', fallback=False)
    }


if __name__ == "__main__":
    with app.app_context():
        try:
            if test_db_connection():
                db.create_all()
                update_schema()
            app.run(debug=True, host=flask_host, port=flask_port)
        except Exception as e:
            print(f"Error: {e}")
            app.run(debug=True, host=flask_host, port=flask_port)
