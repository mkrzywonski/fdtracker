# Freeze Dry Tracker

A web application for tracking freeze-dried food batches, trays, and bags. This tool allows you to manage the entire process, from batch creation to packaging and inventory management, with features like water removal calculation and label generation.

## Features

- **Batch Management**:
  - Create, view, edit, and delete batches.
  - Record trays in each batch with contents and weights.
  - Calculate water removed during freeze-drying.
  - Mark batches as complete when weights are stable.

- **Tray Management**:
  - Add trays to batches with detailed information.
  - Update tray weights to track progress.
  - Edit or delete individual trays.

- **Bag Management**:
  - Package contents from trays into bags.
  - Track bag contents, weights, storage locations, and notes.
  - Print labels with QR codes for each bag.
  - Mark bags as consumed.

- **Inventory Search**:
  - Search batches, trays, and bags by keywords.
  - Filter consumed or unopened bags.

## Technologies Used

- **Backend**: Flask (Python)
- **Frontend**: Jinja2 templates with Bootstrap 5
- **Database**: SQLite
- **Other Tools**:
  - QR code generation
  - ReportLab for PDF label creation

## Installation

1. Clone the repository:
   ```bash
   git clone <repository_url>
   cd <repository_name>
   ```

2. Set up a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Initialize the database:
   ```bash
   python
   >>> from app import db
   >>> db.create_all()
   >>> exit()
   ```

5. Run the development server:
   ```bash
   python app.py
   ```

6. Access the application in your browser at:
   ```
   http://127.0.0.1:5000
   ```

## Usage

1. **Create a Batch**:
   - Navigate to the "New Batch" page and fill in the required information.
   - Add trays to the batch, specifying their contents and initial weights.

2. **Update Trays**:
   - Monitor tray weights during freeze-drying.
   - Enter the final weights when the process is complete.

3. **Package Contents**:
   - Split tray contents into bags, recording their weights and storage locations.
   - Generate labels for bags with QR codes linking to batch details.

4. **Manage Inventory**:
   - View and search through batches, trays, and bags.
   - Mark bags as consumed or update their details.

## Screenshots

- Add a batch
- View batches
- Edit trays and bags
- Generate and print labels

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository.
2. Create a new branch for your feature or bug fix.
3. Submit a pull request with a detailed description.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgements

- [Bootstrap](https://getbootstrap.com/) for responsive design.
- [Flask](https://flask.palletsprojects.com/) for the web framework.
- [SQLite](https://www.sqlite.org/) for the database.
- [ReportLab](https://www.reportlab.com/) for PDF generation.

---

Happy freeze-drying! 😊
