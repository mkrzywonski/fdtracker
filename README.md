# Freeze Dry Tracker

A web application for tracking freeze-dried food batches. This tool allows you to manage the entire process, from batch creation to packaging and inventory management, with features like water removal calculation and label generation.

## Features

- **User Interface**
  - Thumb-friendly design for use on smartphone
  - No need for a full desktop setup in your food prep area
    
- **Batch Management**:
  - Create, view, edit, and delete batches.
  - Record trays in each batch with contents and weights.
  - Automatically calculates water removed during freeze-drying.
  - Mark batches as complete when weights are stable.
  - Attach photos and notes to each batch
    
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

- **Lightweight**
  - Can be run on a Raspberry PI or other small computer.
  - The author runs the app on a Raspberry Pi Zero 2 W

## Technologies Used

- **Backend**: Flask (Python)
- **Frontend**: Jinja2 templates with Bootstrap 5
- **Database**: SQLite or MySQL
- **Other Tools**:
  - QR code generation
  - ReportLab for PDF label creation

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mkrzywonski/fdtracker.git
   cd fdtracker
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

4. Run the development server:
   ```bash
   python app.py
   ```

5. Access the application in your browser at:
   ```
   http://127.0.0.1:5000
   ```

## Usage

1. **Create a Batch**:
   - Navigate to the "New Batch" page and fill in the required information.
   - Add trays to the batch, specifying their contents and initial weights.

2. **Update Tray Weights**:
   - Monitor tray weights during freeze-drying.
   - Keep track of tray weights to determine if you need more drying time.
   - Enter the final weights when the process is complete.

3. **Package Contents**:
   - Split tray contents into bags, recording their weights and storage locations.
   - Generate labels for bags with QR codes linking to batch details.

4. **Manage Inventory**:
   - View and search through batches, trays, and bags.
   - Mark bags as consumed or update their details.

## License

This project is licensed under the GNU General Public License. See the [LICENSE](LICENSE) file for details.

---

Happy freeze-drying! 😊
