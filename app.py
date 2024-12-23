from flask import Flask, render_template, request, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from io import BytesIO
import qrcode
from reportlab.platypus import SimpleDocTemplate, Paragraph, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from markupsafe import Markup
import re

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///freezedry.db'
db = SQLAlchemy(app)
PER_PAGE = 25  # Number of items per page

class Bag(db.Model):
    id = db.Column(db.String(20), primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('batch.id'), nullable=False)
    contents = db.Column(db.String(100), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(100))
    notes = db.Column(db.Text)
    water_needed = db.Column(db.Float)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    consumed_date = db.Column(db.DateTime)

class Tray(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('batch.id'), nullable=False)
    contents = db.Column(db.String(100), nullable=False)
    starting_weight = db.Column(db.Float)
    ending_weight = db.Column(db.Float)
    previous_weight = db.Column(db.Float)
    notes = db.Column(db.Text)
    position = db.Column(db.Integer, nullable=False)  # Tray position in the machine

class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_date = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='In Progress')
    trays = db.relationship('Tray', backref='batch', lazy=True, 
                          order_by='Tray.position',
                          cascade='all, delete-orphan')
    bags = db.relationship('Bag', backref='batch', lazy=True, 
                          order_by='Bag.created_date',
                          cascade='all, delete-orphan')
    @property
    def total_starting_weight(self):
        return sum(tray.starting_weight or 0 for tray in self.trays)

    @property
    def total_ending_weight(self):
        return sum(tray.ending_weight or 0 for tray in self.trays)

@app.route('/')
def index():
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

        query = query.outerjoin(Tray).outerjoin(Bag).filter(
            db.or_(
                Batch.id == search_id,
                Batch.notes.ilike(f"%{search_query}%"),
                Tray.contents.ilike(f"%{search_query}%"),
                Tray.notes.ilike(f"%{search_query}%"),
                Bag.contents.ilike(f"%{search_query}%"),
                Bag.notes.ilike(f"%{search_query}%"),
            )
        )

    if expanded_batch:
        # Get all batch IDs sorted by ID with most recent first
        all_batches = query.order_by(Batch.id.desc()).all()
        batch_ids = [batch.id for batch in all_batches]

        # Determine the page where the expanded_batch resides
        if expanded_batch in batch_ids:
            batch_index = batch_ids.index(expanded_batch)
            page = (batch_index // PER_PAGE) + 1  # Use PER_PAGE

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
        batch = Batch(
            notes=request.form['batch_notes']
        )
        db.session.add(batch)
        
        # Handle multiple trays
        tray_count = int(request.form['tray_count'])
        for i in range(tray_count):
            if request.form.get(f'contents_{i}'):  # Only add trays with contents
                tray = Tray(
                    contents=request.form[f'contents_{i}'],
                    starting_weight=float(request.form[f'starting_weight_{i}']),
                    notes=request.form.get(f'notes_{i}', ''),
                    position=i + 1
                )
                batch.trays.append(tray)
        
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('add_batch.html')

@app.route('/complete/<int:id>', methods=['POST'])
def complete_batch(id):
    batch = Batch.query.get_or_404(id)
    batch.status = 'Completed'
    batch.end_date = datetime.utcnow()
    
    # Update each tray's final weight
    for tray in batch.trays:
        tray_id = str(tray.id)
        if f'ending_weight_{tray_id}' in request.form:
            tray.ending_weight = float(request.form[f'ending_weight_{tray_id}'])
    
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete/<int:id>', methods=['POST'])
def delete_batch(id):
    batch = Batch.query.get_or_404(id)
    db.session.delete(batch)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/add_bag/<int:tray_id>', methods=['GET', 'POST'])
def add_bag(tray_id):
    tray = Tray.query.get_or_404(tray_id)
    if request.method == 'POST':
        weight_loss_ratio = (tray.starting_weight - tray.ending_weight) / tray.ending_weight
        bag_weight = float(request.form['weight'])
        water_needed = weight_loss_ratio * bag_weight
        
        # Find highest bag number for this batch
        highest_bag = Bag.query.filter_by(batch_id=tray.batch_id).order_by(Bag.id.desc()).first()
        print(f"Highest Bag: {highest_bag}")
            
        next_number = 1
        if highest_bag:
            current_highest = int(highest_bag.id.split('-')[1])
            next_number = current_highest + 1
            
        bag_id = f"{tray.batch_id:08d}-{next_number}"
        
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
        return redirect(url_for('index', expanded_batch=tray.batch_id))
    return render_template('add_bag.html', tray=tray)

@app.route('/delete_bag/<string:id>', methods=['POST'])
def delete_bag(id):
    bag = Bag.query.get_or_404(id)
    db.session.delete(bag)
    db.session.commit()
    return redirect(url_for('index', expanded_batch=bag.batch_id))

@app.route('/consume_bag/<string:id>', methods=['POST'])
def consume_bag(id):
    bag = Bag.query.get_or_404(id)
    bag.consumed_date = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('index', expanded_batch=bag.batch_id))

@app.route('/print_label/<string:id>')
def print_label(id):
    bag = Bag.query.get_or_404(id)

    # Create QR code with batch URL
    batch_url = url_for('index', expanded_batch=bag.batch_id, _external=True)
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

    # Page setup
    from reportlab.platypus import Frame, PageTemplate
    from reportlab.lib.pagesizes import inch

    PAGE_WIDTH, PAGE_HEIGHT = 4 * inch, 6 * inch
    MARGIN = 0.25 * inch

    # Function to draw footer (QR code and URL) on every page
    def draw_footer(canvas, doc):
        qr_img = RLImage(qr_buffer, width=1.5 * inch, height=1.5 * inch)
        qr_x = (PAGE_WIDTH - qr_img.drawWidth) / 2
        qr_y = 0.5 * inch
        qr_img.drawOn(canvas, qr_x, qr_y)

        url_style = ParagraphStyle(
            'URL',
            fontSize=8,
            alignment=1  # Center alignment
        )
        url_paragraph = Paragraph(batch_url, url_style)
        url_width, url_height = url_paragraph.wrap(PAGE_WIDTH - 2 * MARGIN, 0)
        url_x = (PAGE_WIDTH - url_width) / 2
        canvas.saveState()
        url_paragraph.drawOn(canvas, url_x, qr_y - url_height - 10)  # Position below QR code
        canvas.restoreState()

    # Set up the document
    doc = SimpleDocTemplate(
        buffer,
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        rightMargin=MARGIN,
        leftMargin=MARGIN,
        topMargin=MARGIN,  # Leave space at the bottom for the footer
        bottomMargin=2 * inch  # QR code and URL will fit here
    )

    # Create styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=10
    )
    normal_style = ParagraphStyle(
        'CustomBody',
        parent=styles['Normal'],
        fontSize=14,
        leading=16
    )

    # Build content elements
    elements = []
    elements.append(Paragraph(f"Bag #{bag.id} {bag.created_date.strftime('%Y-%m-%d')}", title_style))
    elements.append(Paragraph(f"Contents: {bag.contents}", normal_style))
    elements.append(Paragraph(f"Weight: {bag.weight}g", normal_style))
    elements.append(Paragraph(f"Water Needed: {bag.water_needed:.1f}g", normal_style))
    if bag.location:
        elements.append(Paragraph(f"Location: {bag.location}", normal_style))
    if bag.notes:
        elements.append(Paragraph(f"Notes: {bag.notes}", normal_style))

    # Set up a page template with the footer
    doc.addPageTemplates(
        PageTemplate(
            frames=[
                Frame(MARGIN, MARGIN, PAGE_WIDTH - 2 * MARGIN, PAGE_HEIGHT - 0.5 * inch)
            ],
            onPage=draw_footer  # Call the footer drawing function on every page
        )
    )

    # Generate the PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=f'bag_{bag.id}_label.pdf',
        mimetype='application/pdf'
    )


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

        db.session.commit()
        return redirect(url_for('index', expanded_batch=batch.id))

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
            return redirect(url_for('index', id=batch_id))
        else:
            tray.contents = request.form['contents']
            tray.starting_weight = float(request.form['starting_weight']) if request.form['starting_weight'] else None
            tray.ending_weight = float(request.form['ending_weight']) if request.form['ending_weight'] else None
            tray.notes = request.form['notes']
            db.session.commit()
            return redirect(url_for('index', expanded_batch=tray.batch_id))
    return render_template('edit_tray.html', tray=tray)

@app.route('/edit_bag/<string:id>', methods=['GET', 'POST'])
def edit_bag(id):
    bag = Bag.query.get_or_404(id)
    if request.method == 'POST':
        # Handle delete action
        if 'delete' in request.form:
            batch_id = bag.batch_id
            db.session.delete(bag)
            db.session.commit()
            return redirect(url_for('edit_batch', id=batch_id))
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
            return redirect(url_for('index', expanded_batch=bag.batch_id))
    return render_template('edit_bag.html', bag=bag)

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
    return redirect(url_for('index', expanded_batch=batch.id))

@app.route('/view_bags')
def view_bags():
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search', '').strip()
    expanded_bag = request.args.get('expanded_bag', type=str)

    # Base query
    query = Bag.query

    # Apply search filter if a search query is provided
    if search_query:
        query = query.filter(
            db.or_(
                Bag.id.ilike(f"%{search_query}%"),
                Bag.contents.ilike(f"%{search_query}%"),
                Bag.notes.ilike(f"%{search_query}%"),
            )
        )

    if expanded_bag:
        # Get all batch IDs sorted by ID with most recent first
        all_bags = query.order_by(Bag.id.desc()).all()
        bag_ids = [bag.id for bag in all_bags]

        # Determine the page where the expanded_batch resides
        if expanded_bag in bag_ids:
            bag_index = bag_ids.index(expanded_bag)
            page = (bag_index // PER_PAGE) + 1  # Use PER_PAGE

    # Paginate the filtered results
    pagination = query.order_by(Bag.id.desc()).paginate(page=page, per_page=PER_PAGE)
    bags = pagination.items

    return render_template(
        'bags.html',
        bags=bags,
        pagination=pagination,
        expanded_bag=expanded_bag,
        search_query=search_query
    )

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
