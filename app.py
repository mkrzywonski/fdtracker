# Standard library imports
import os
import re
from datetime import datetime
from io import BytesIO
from urllib.parse import urlparse
import configparser
from zipfile import ZipFile, BadZipFile
import shutil
import hashlib
import json

# Third-party imports
from flask import (
    Flask, 
    render_template, 
    request, 
    redirect, 
    url_for, 
    send_file, 
    send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from markupsafe import Markup
import qrcode
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import inch
from reportlab.lib.utils import ImageReader, simpleSplit



# Read the configuration file
config = configparser.ConfigParser()
config.read('config.ini')

# Get internal Flask binding settings
flask_host = config.get('server', 'flask_host', fallback='127.0.0.1')
flask_port = config.getint('server', 'flask_port', fallback=5000)

# Get public-facing URL
public_url = config.get('server', 'public_url', fallback=None)

# Parse the public URL into components (if provided)
if public_url:
    parsed_url = urlparse(public_url)
    public_host = parsed_url.netloc  # Includes domain and port
    public_scheme = parsed_url.scheme  # http or https
else:
    public_host = None
    public_scheme = None

# Flask app setup
app = Flask(__name__)

# Set SERVER_NAME if the public host is configured
if public_host:
    app.config['SERVER_NAME'] = public_host

# Middleware for reverse proxy headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///freezedry.db'
db = SQLAlchemy(app)
PER_PAGE = 25  # Number of items per page

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

class Batch(db.Model):
    __tablename__ = 'batch'
    id = db.Column(db.Integer, primary_key=True, index=True)
    start_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_date = db.Column(db.DateTime)
    notes = db.Column(db.Text, index=True)  # Add index for better search performance
    status = db.Column(db.String(20), default='In Progress')

    # Relationships with cascading deletes and lazy loading
    trays = db.relationship(
        'Tray', backref='batch', lazy='dynamic', cascade='all, delete-orphan'
    )
    bags = db.relationship(
        'Bag', backref='batch', lazy='dynamic', cascade='all, delete-orphan'
    )
    photos = db.relationship(
        'Photo', backref='batch', lazy='dynamic', cascade='all, delete-orphan'
    )
    @property
    def total_starting_weight(self):
        return sum(tray.starting_weight or 0 for tray in self.trays)
    @property
    def total_ending_weight(self):
        return sum(tray.ending_weight or 0 for tray in self.trays)


class Tray(db.Model):
    __tablename__ = 'tray'
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(
        db.Integer, db.ForeignKey('batch.id', ondelete='CASCADE'), nullable=False, index=True
    )
    contents = db.Column(db.String(100), nullable=False, index=True)
    starting_weight = db.Column(db.Float)
    ending_weight = db.Column(db.Float)
    previous_weight = db.Column(db.Float)
    notes = db.Column(db.Text, index=True)
    position = db.Column(db.Integer, nullable=False)  # Tray position in the machine


class Bag(db.Model):
    __tablename__ = 'bag'
    id = db.Column(db.String(20), primary_key=True)
    batch_id = db.Column(
        db.Integer, db.ForeignKey('batch.id', ondelete='CASCADE'), nullable=False, index=True
    )
    contents = db.Column(db.String(100), nullable=False, index=True)
    weight = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(100), index=True)
    notes = db.Column(db.Text, index=True)
    water_needed = db.Column(db.Float)
    created_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    consumed_date = db.Column(db.DateTime)

class Photo(db.Model):
    __tablename__ = 'photo'
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('batch.id', ondelete='CASCADE'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

@app.route('/')
def view_batches():
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search', '').strip()
    expanded_batch = request.args.get('expanded_batch', type=int)

    # Base query
    query = Batch.query

    # Apply search filter if a search query is provided
    if search_query:
        try:
            search_id = int(search_query)
        except ValueError:
            search_id = None

        filters = [Batch.id == search_id, Batch.notes.ilike(f"%{search_query}%")]

        if search_query:
            filters.extend([
                Tray.contents.ilike(f"%{search_query}%"),
                Tray.notes.ilike(f"%{search_query}%"),
                Bag.contents.ilike(f"%{search_query}%"),
                Bag.notes.ilike(f"%{search_query}%"),
            ])

        query = query.outerjoin(Tray).outerjoin(Bag).filter(or_(*filters))

    # Handle expanded batch
    if expanded_batch:
        all_batches = query.order_by(Batch.id.desc()).all()
        batch_ids = [batch.id for batch in all_batches]

        if expanded_batch in batch_ids:
            batch_index = batch_ids.index(expanded_batch)
            page = (batch_index // PER_PAGE) + 1

    # Paginate the filtered results
    pagination = query.order_by(Batch.id.desc()).paginate(page=page, per_page=PER_PAGE)
    batches = pagination.items

    return render_template(
        'batches.html',
        batches=batches,
        pagination=pagination,
        expanded_batch=expanded_batch,
        search_query=search_query
    )

@app.route('/add', methods=['GET', 'POST'])
def add_batch():
    if request.method == 'POST':
        tray_count = int(request.form['tray_count'])
        error_messages = []
        trays = []

        # Validate tray weights and gather data
        for i in range(tray_count):
            contents = request.form.get(f'contents_{i}', '')
            starting_weight = request.form.get(f'starting_weight_{i}', '')
            notes = request.form.get(f'notes_{i}', '')

            try:
                starting_weight = float(starting_weight)
                if starting_weight <= 0:
                    error_messages.append(f"Tray {i + 1}: Initial weight must be greater than 0.")
            except (ValueError, TypeError):
                error_messages.append(f"Tray {i + 1}: Initial weight must be a valid number.")

            # Append the entered data for each tray (preserving input even if invalid)
            trays.append({
                "contents": contents,
                "starting_weight": starting_weight if isinstance(starting_weight, float) else '',
                "notes": notes
            })

        # If there are errors, return the form with error messages and pre-filled data
        if error_messages:
            return render_template(
                'add_batch.html',
                error_messages=error_messages,
                tray_count=tray_count,
                trays=trays,
                batch_notes=request.form.get('batch_notes', '')
            )

        # Create a new batch
        batch = Batch(notes=request.form['batch_notes'])
        db.session.add(batch)

        # Add trays to the batch
        for i, tray_data in enumerate(trays):
            tray = Tray(
                contents=tray_data["contents"],
                starting_weight=tray_data["starting_weight"],
                notes=tray_data["notes"],
                position=i + 1
            )
            batch.trays.append(tray)

        db.session.commit()
        return redirect(url_for('view_batches', expanded_batch=batch.id))

    # Default state for a new form
    return render_template('add_batch.html', tray_count=1, trays=[], batch_notes='')

@app.route('/complete_batch/<int:id>', methods=['POST'])
def complete_batch(id):
    batch = Batch.query.get_or_404(id)
    error_messages = []

    # Validate weights for each tray
    for tray in batch.trays:
        ending_weight = request.form.get(f'ending_weight_{tray.id}', None)
        try:
            ending_weight = float(ending_weight)
            if ending_weight <= 0:
                error_messages.append(f"Tray {tray.position}: Final weight must be greater than 0.")
            elif ending_weight > tray.starting_weight:
                error_messages.append(f"Tray {tray.position}: Final weight cannot exceed the initial weight.")
        except (ValueError, TypeError):
            error_messages.append(f"Tray {tray.position}: Final weight must be a valid number.")

    # If there are validation errors, re-render the batch page with errors
    if error_messages:
        return render_template(
            'batches.html',
            batches=[batch],  # Only show the current batch
            expanded_batch=id,
            error_messages=error_messages
        )

    # Update tray weights and mark batch as complete
    for tray in batch.trays:
        ending_weight = float(request.form[f'ending_weight_{tray.id}'])
        tray.ending_weight = ending_weight

    batch.status = 'Complete'
    batch.end_date = datetime.utcnow()
    db.session.commit()

    return redirect(url_for('view_batches'))

@app.route('/delete/<int:id>', methods=['POST'])
def delete_batch(id):
    batch = Batch.query.get_or_404(id)
    
    # Delete all photo files associated with this batch
    for photo in batch.photos:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    
    db.session.delete(batch)
    db.session.commit()
    return redirect(url_for('view_batches'))

@app.route('/add_bag/<int:tray_id>', methods=['GET', 'POST'])
def add_bag(tray_id):
    tray = Tray.query.get_or_404(tray_id)
    if request.method == 'POST':
        if tray.ending_weight is None or tray.ending_weight <= 0:
            tray.ending_weight = tray.starting_weight
        weight_loss_ratio = (tray.starting_weight - tray.ending_weight) / tray.ending_weight
        bag_weight = float(request.form['weight'])
        water_needed = weight_loss_ratio * bag_weight
        water_needed = round(water_needed, 1)
        
        # Query all bag IDs for the current batch
        all_bag_ids = db.session.query(Bag.id).filter(Bag.batch_id == tray.batch_id).all()
        all_bag_ids = [bag_id[0] for bag_id in all_bag_ids]  # Flatten the list

        # Extract the numerical parts after the "-" and sort numerically
        used_numbers = [
            int(bag_id.split('-')[1]) for bag_id in all_bag_ids if '-' in bag_id
        ]
        next_number = max(used_numbers, default=0) + 1  # Get the next number
            
        bag_id = f"{tray.batch_id:08d}-{next_number:02d}"
        
        bag = Bag(
            id=bag_id,
            batch_id=tray.batch_id,
            contents=request.form['contents'],
            weight=bag_weight,
            location=request.form['location'],
            notes=request.form['notes'],
            water_needed=water_needed
        )
        db.session.add(bag)
        db.session.commit()
        return redirect(url_for('view_batches', expanded_batch=tray.batch_id))
    return render_template('add_bag.html', tray=tray)

@app.route('/delete_bag/<string:id>', methods=['POST'])
def delete_bag(id):
    bag = Bag.query.get_or_404(id)
    db.session.delete(bag)
    db.session.commit()
    return redirect(url_for('view_batches', expanded_batch=bag.batch_id))

@app.route('/consume_bag/<string:id>', methods=['POST'])
def consume_bag(id):
    bag = Bag.query.get_or_404(id)
    bag.consumed_date = datetime.utcnow()
    db.session.commit()
    next_url = request.args.get('next')
    if next_url:
        return redirect(next_url)
    return redirect(url_for('view_batches', expanded_batch=bag.batch_id))

@app.route('/edit_batch/<int:id>', methods=['GET', 'POST'])
def edit_batch(id):
    batch = Batch.query.get_or_404(id)
    if request.method == 'POST':
        batch.notes = request.form['batch_notes']
        batch.start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d')
        end_date = request.form.get('end_date')
        batch.end_date = datetime.strptime(end_date, '%Y-%m-%d') if end_date else None

        # Deleting trays
        trays_to_delete = request.form.getlist('delete_tray')
        for tray_id in trays_to_delete:
            tray = Tray.query.get(tray_id)
            db.session.delete(tray)

        # Deleting bags
        bags_to_delete = request.form.getlist('delete_bag')
        for bag_id in bags_to_delete:
            bag = Bag.query.get(bag_id)
            db.session.delete(bag)

        # Photo Captions
        for photo in batch.photos:
            caption_key = f'caption-{photo.id}'
            if caption_key in request.form:
                photo.caption = request.form[caption_key]

        # Deleting Photos
        photos_to_delete = request.form.getlist('delete_photo')
        for photo_id in photos_to_delete:
            photo = Photo.query.get(photo_id)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
            if os.path.exists(file_path):
                os.remove(file_path)
            db.session.delete(photo)
        db.session.commit()
        return redirect(url_for('view_batches', expanded_batch=batch.id))

    return render_template('edit_batch.html', batch=batch)

@app.route('/edit_tray/<int:id>', methods=['GET', 'POST'])
def edit_tray(id):
    tray = Tray.query.get_or_404(id)
    if request.method == 'POST':
        # Handle form submission to update tray details or delete the tray
        if 'delete' in request.form:
            batch_id = tray.batch_id
            db.session.delete(tray)
            db.session.commit()
            return redirect(url_for('view_batches', expanded_batch=batch_id))
        else:
            tray.contents = request.form['contents']
            tray.starting_weight = float(request.form['starting_weight']) if request.form['starting_weight'] else None
            tray.ending_weight = float(request.form['ending_weight']) if request.form['ending_weight'] else None
            tray.notes = request.form['notes']
            db.session.commit()
            return redirect(url_for('view_batches', expanded_batch=tray.batch_id))
    return render_template('edit_tray.html', tray=tray)

@app.route('/edit_bag/<string:id>', methods=['GET', 'POST'])
def edit_bag(id):
    bag = Bag.query.get_or_404(id)
    next_url = request.args.get('next')
    if not next_url:
        next_url = request.form['next']
    if request.method == 'POST':
        # Handle delete action
        if 'delete' in request.form:
            batch_id = bag.batch_id
            db.session.delete(bag)
            db.session.commit()
            return redirect(next_url)
        else:
            # Update bag details
            bag.contents = request.form['contents']
            bag.weight = float(request.form['weight']) if request.form['weight'] else None
            bag.location = request.form['location']
            bag.notes = request.form['notes']
            bag.water_needed = float(request.form['water_needed']) if request.form['water_needed'] else None
            bag.created_date = datetime.strptime(request.form['created_date'], '%Y-%m-%d') if request.form['created_date'] else None
            consumed_date = request.form['consumed_date']
            bag.consumed_date = datetime.strptime(consumed_date, '%Y-%m-%d') if consumed_date else None

            db.session.commit()
        if next_url:
            return redirect(next_url)            
    return render_template('edit_bag.html', bag=bag, next=next_url)

@app.template_filter('highlight')
def highlight_search(text, search):
    if not search:
        return text
    escaped_search = re.escape(search)
    return Markup(re.sub(f"({escaped_search})", r"<mark>\1</mark>", text, flags=re.IGNORECASE))

@app.route('/update_weight/<int:batch_id>', methods=['POST'])
def update_weight(batch_id):
    batch = Batch.query.get_or_404(batch_id)

    for tray in batch.trays:
        tray_id = str(tray.id)
        # Retrieve the weight entered by the user from the form
        entered_weight = request.form.get(f'ending_weight_{tray_id}')
        if entered_weight:
            # Save the entered weight as the tray's previous weight
            tray.previous_weight = float(entered_weight)

    # Commit the changes to the database
    db.session.commit()

    # Redirect back to the batch details page with the batch expanded
    return redirect(url_for('view_batches', expanded_batch=batch.id))

@app.route('/view_bags')
def view_bags():
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search', '').strip()
    expanded_bag = request.args.get('expanded_bag', type=str)
    unopened = request.args.get('unopened', type=str) == 'on'

    # Base query
    query = Bag.query

    # Apply search filter
    if search_query:
        query = query.filter(
            or_(
                Bag.id.ilike(f"%{search_query}%"),
                Bag.contents.ilike(f"%{search_query}%"),
                Bag.notes.ilike(f"%{search_query}%"),
            )
        )

    # Filter for unopened bags
    if unopened:
        query = query.filter(Bag.consumed_date.is_(None))

    # Handle expanded bag
    if expanded_bag:
        all_bags = query.order_by(Bag.id.desc()).all()
        bag_ids = [bag.id for bag in all_bags]

        if expanded_bag in bag_ids:
            bag_index = bag_ids.index(expanded_bag)
            page = (bag_index // PER_PAGE) + 1

    # Paginate the filtered results
    pagination = query.order_by(Bag.id.desc()).paginate(page=page, per_page=PER_PAGE)
    bags = pagination.items

    return render_template(
        'bags.html',
        bags=bags,
        pagination=pagination,
        expanded_bag=expanded_bag,
        search_query=search_query,
        unopened=unopened
    )

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        directory='static',
        path='freezedryer.svg',
        mimetype='image/svg+xml'
    )

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def resize_and_convert_image(image_path, max_width=800, max_height=600, quality=80):
    with Image.open(image_path) as img:
        # Convert to RGB if needed (in case of PNG with transparency)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        # Resize maintaining aspect ratio
        img.thumbnail((max_width, max_height))
        
        # Get the output path with .jpg extension
        output_path = os.path.splitext(image_path)[0] + '.jpg'
        
        # Save as JPEG with specified quality
        img.save(output_path, 'JPEG', quality=quality)
        
        # Remove original file if different from output
        if output_path != image_path:
            os.remove(image_path)
            
        return os.path.basename(output_path)

@app.route('/add_photo/<int:batch_id>', methods=['GET', 'POST'])
def add_photo(batch_id):
    batch = Batch.query.get_or_404(batch_id)
    if request.method == 'POST':
        if 'photo' not in request.files:
            return render_template('add_photo.html', batch=batch, error="No file part")

        file = request.files['photo']
        caption = request.form.get('caption', '')
        
        if file.filename == '':
            return render_template('add_photo.html', batch=batch, error="No selected file")
        if file and allowed_file(file.filename):
            photo = Photo(batch_id=batch_id, filename='temp', caption=caption)
            db.session.add(photo)
            db.session.flush()

            original_name = secure_filename(file.filename)
            temp_filename = f"IMG_{photo.id}{os.path.splitext(original_name)[1]}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
            file.save(filepath)
            
            final_filename = resize_and_convert_image(filepath)
            photo.filename = final_filename
            
            db.session.commit()
            return redirect(url_for('view_batches', expanded_batch=batch_id))
    
    return render_template('add_photo.html', batch=batch)

@app.route('/print_label/<string:id>')
def print_label(id):
    bag = Bag.query.get_or_404(id)

    # Create QR code with batch URL
    batch_url = url_for('view_bags', expanded_bag=bag.id, _external=True)
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(batch_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Save QR code to memory
    qr_buffer = BytesIO()
    qr_img.save(qr_buffer)
    qr_buffer.seek(0)

    # Create PDF in memory
    buffer = BytesIO()

    # Create the PDF canvas
    c = canvas.Canvas(buffer, pagesize=(4*inch, 6*inch))
    
    # Draw the border and line
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.02 * inch)
    c.roundRect(0.1 * inch, 0.1 * inch, 3.8 * inch, 5.8 * inch, 0.25 * inch)
    c.line(0.1 * inch, 5.4 * inch, 3.9 * inch, 5.4 * inch)
    
    # Add batch ID and date text
    c.setFont('Helvetica-Bold', 12)
    c.drawString(0.2 * inch, 5.6 * inch, f"Batch: {bag.batch_id:08d}")
    date_text = bag.batch.start_date.strftime('%Y-%m-%d')
    c.drawRightString(3.8 * inch, 5.6 * inch, date_text)
    
    # Add centered contents with text wrapping
    from reportlab.lib.utils import simpleSplit
    c.setFont('Helvetica-Bold', 14)
    text_width = 3.6 * inch  # Allowing for margins
    wrapped_lines = simpleSplit(bag.contents, c._fontname, c._fontsize, text_width)
    y = 5.0 * inch  # 1 inch from top
    for line in wrapped_lines:
        text_length = c.stringWidth(line)
        x = (4 * inch - text_length) / 2  # Center the text
        c.drawString(x, y, line)
        y -= 20  # Move down for next line

    # Add details below contents
    y -= 10  # Extra space after contents
    c.setFont('Helvetica', 12)  # Switch to normal weight
    x = 0.2 * inch  # Left margin
    
    # Fixed details
    c.drawString(x, y, f"Bag ID: {bag.id}")
    y -= 15
    c.drawString(x, y, f"Freeze Dried Weight: {bag.weight}g")
    y -= 15
    original_weight = round(bag.weight + bag.water_needed, 1)
    c.drawString(x, y, f"Original Weight: ~{original_weight}g")
    y -= 15
    w = bag.water_needed
    water_needed = f"{water_volume_metric(w)} ({water_volume_imperial(w)})"
    c.drawString(x, y, f"Water Needed: ~{water_needed}")
    y -= 15

    # Wrapping text for location
    if bag.location:
        wrapped_location = simpleSplit(f"Location: {bag.location}", c._fontname, c._fontsize, text_width)
        for line in wrapped_location:
            c.drawString(x, y, line)
            y -= 15

    # Wrapping text for notes
    if bag.notes:
        y -= 10  # Extra space before notes
        wrapped_notes = simpleSplit(f"{bag.notes}", c._fontname, c._fontsize, text_width)
        for line in wrapped_notes:
            c.drawString(x, y, line)
            y -= 15

    # Add QR code at bottom
    qr_width = qr_height = 1 * inch
    qr_x = (4 * inch - qr_width) / 2  # Center horizontally
    qr_y = 0.2 * inch  # Position from bottom
    c.drawImage(ImageReader(qr_buffer), qr_x, qr_y, qr_width, qr_height)
    
    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=f'bag_{bag.id}_label.pdf',
        mimetype='application/pdf'
    )

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

def calculate_backup_hash(db_path, jpg_files):
    """Calculate a hash of the database and jpg files"""
    hasher = hashlib.sha256()
    
    # Hash database content
    with open(db_path, 'rb') as f:
        hasher.update(f.read())
        
    # Hash jpg files in sorted order for consistency
    for jpg_file in sorted(jpg_files):
        with open(jpg_file, 'rb') as f:
            hasher.update(f.read())
            
    return hasher.hexdigest()

@app.route('/backup')
def create_backup():
    db_path = os.path.join(app.instance_path, 'freezedry.db')
    
    # Quiesce database
    db.session.commit()
    db.session.remove()
    db.engine.dispose()
    
    # Get list of jpg files
    jpg_files = []
    for root, _, files in os.walk(UPLOAD_FOLDER):
        for file in files:
            if file.lower().endswith('.jpg'):
                jpg_files.append(os.path.join(root, file))
                
    # Calculate hash
    backup_hash = calculate_backup_hash(db_path, jpg_files)
    
    # Create backup with manifest
    backup = BytesIO()
    with ZipFile(backup, 'w') as zip_file:
        # Add database
        zip_file.write(db_path, 'freezedry.db')
        
        # Add jpg files
        for file_path in jpg_files:
            arcname = os.path.relpath(file_path, start='.')
            zip_file.write(file_path, arcname)
            
        # Add manifest with hash
        manifest = {
            'hash': backup_hash,
            'timestamp': datetime.now().isoformat()
        }
        zip_file.writestr('manifest.json', json.dumps(manifest))
    
    backup.seek(0)
    return send_file(
        backup,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'fdtracker_backup_{datetime.now().strftime("%Y%m%d")}.zip'
    )

import magic  # For file type detection

def validate_backup_contents(zip_file):
    """Validate all files in the backup archive"""
    valid_files = {'freezedry.db', 'manifest.json'}
    uploads_prefix = 'static/uploads/'
    
    # Check that all required files exist
    found_files = set(name for name in zip_file.namelist() if '/' not in name)
    missing_files = valid_files - found_files
    if missing_files:
        return False, f"Missing required files: {', '.join(missing_files)}"
    
    # Validate each file
    for file_path in zip_file.namelist():
        if file_path == 'freezedry.db':
            content = zip_file.read(file_path)
            mime = magic.from_buffer(content, mime=True)
            if mime != 'application/x-sqlite3':
                return False, "Invalid database file format"
                
        elif file_path == 'manifest.json':
            continue
            
        elif file_path.startswith(uploads_prefix) and file_path.lower().endswith('.jpg'):
            content = zip_file.read(file_path)
            mime = magic.from_buffer(content, mime=True)
            if mime not in ('image/jpeg', 'image/jpg'):
                return False, f"Invalid image format: {file_path}"
                
        else:
            return False, f"Unexpected file in backup: {file_path}"
            
    return True, None


@app.route('/restore', methods=['GET', 'POST'])
def restore_backup():
    if request.method == 'GET':
        return render_template('restore_backup.html')
        
    if 'backup_file' not in request.files:
        return render_template('restore_backup.html', error="No backup file provided")
        
    backup = request.files['backup_file']
    
    if backup.filename == '':
        return render_template('restore_backup.html', error="No file selected")

    try:
        with ZipFile(backup, 'r') as zip_file:
            # Validate all files before extraction
            is_valid, error_msg = validate_backup_contents(zip_file)
            if not is_valid:
                return render_template('restore_backup.html', error=error_msg)
                
            # Extract to temp directory for hash validation
            zip_file.extractall('restore_temp')
            
            # Read manifest
            with open('restore_temp/manifest.json') as f:
                manifest = json.load(f)
                
            # Get jpg files
            jpg_files = []
            for root, _, files in os.walk('restore_temp/static/uploads'):
                for file in files:
                    if file.lower().endswith('.jpg'):
                        jpg_files.append(os.path.join(root, file))
                        
            # Verify hash
            current_hash = calculate_backup_hash(
                'restore_temp/freezedry.db',
                jpg_files
            )
            if current_hash != manifest['hash']:
                return render_template('restore_backup.html',
                    error="Invalid backup: File verification failed")
                
            # Close database connections
            db.session.remove()
            db.engine.dispose()
            
            # Restore files
            db_path = os.path.join(app.instance_path, 'freezedry.db')
            shutil.copy2('restore_temp/freezedry.db', db_path)
            shutil.rmtree(UPLOAD_FOLDER)
            shutil.copytree('restore_temp/static/uploads', UPLOAD_FOLDER)
            
    except (BadZipFile, json.JSONDecodeError, KeyError):
        return render_template('restore_backup.html',
            error="Invalid backup file format")
    finally:
        shutil.rmtree('restore_temp', ignore_errors=True)
    
    # Reconnect to database
    db.create_all()
    
    return redirect(url_for('view_batches'))



if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host=flask_host, port=flask_port)
