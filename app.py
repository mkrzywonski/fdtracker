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
from sqlalchemy import or_


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///freezedry.db'
db = SQLAlchemy(app)
PER_PAGE = 25  # Number of items per page

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
        return redirect(url_for('view_batches'))

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

    # Page setup
    from reportlab.platypus import Frame, PageTemplate
    from reportlab.lib.pagesizes import inch

    PAGE_WIDTH, PAGE_HEIGHT = 4 * inch, 6 * inch
    MARGIN = 0.25 * inch

    # Function to draw footer (QR code and URL) on every page
    def draw_footer(canvas, doc):
        qr_img = RLImage(qr_buffer, width=1.5 * inch, height=1.5 * inch)
        qr_x = (PAGE_WIDTH - qr_img.drawWidth) / 2
        qr_y = 0.25 * inch
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
        url_paragraph.drawOn(canvas, url_x, qr_y - url_height)  # Position below QR code
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
