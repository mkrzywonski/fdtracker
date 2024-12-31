from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

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
