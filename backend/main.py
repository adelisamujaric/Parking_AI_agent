import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import shutil
from backend.database import init_db, DB_PATH
from pydantic import BaseModel
from backend.ocr import read_plate
from backend.utils import crop_plate
from ultralytics import YOLO
import shutil
from datetime import datetime

# Folder za odbijene detekcije
REJECTED_DIR = "backend/rejected"
os.makedirs(os.path.join(REJECTED_DIR, "first"), exist_ok=True)
os.makedirs(os.path.join(REJECTED_DIR, "zoom"), exist_ok=True)

app = FastAPI()
init_db()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Kreiraj uploads folder ako ne postoji
UPLOAD_DIR = "backend/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# YOLO model
model = YOLO("backend/weights/best.pt")


# --------------------------------------------------------
# BASIC TEST ROUTE
# --------------------------------------------------------
@app.get("/")
def root():
    return {"message": "AI Parking Agent backend is running!"}


# --------------------------------------------------------
# YOLO DETECT – for bounding boxes on frontend
# --------------------------------------------------------
@app.post("/detect")
async def detect_image(file: UploadFile = File(...)):
    temp_path = os.path.join(UPLOAD_DIR, "temp_image.jpg")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    results = model(temp_path)
    detections = []

    for box in results[0].boxes:
        xyxy = box.xyxy[0].tolist()
        cls_id = int(box.cls[0])
        cls_name = model.names[cls_id]
        conf = float(box.conf[0])

        detections.append({
            "box": xyxy,
            "class": cls_name,
            "confidence": conf
        })

    return {"detections": detections}


# --------------------------------------------------------
# OCR DETECTION (optional)
# --------------------------------------------------------
@app.post("/detect_plate")
async def detect_plate(file: UploadFile = File(...)):
    temp_path = os.path.join(UPLOAD_DIR, "temp_plate_source.jpg")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    results = model(temp_path)
    boxes = results[0].boxes

    plate_box = None
    for box in boxes:
        cls_id = int(box.cls[0])
        cls_name = model.names[cls_id]

        if cls_name.lower() == "tablica":
            plate_box = box.xyxy[0].tolist()
            break

    if plate_box is None:
        return {"plate": None, "error": "Plate not detected"}

    crop_path = crop_plate(temp_path, plate_box)
    plate_text = read_plate(crop_path)

    return {
        "plate": plate_text if plate_text else "Not detected",
        "bbox": plate_box
    }


# --------------------------------------------------------
# DRIVER LOOKUP
# --------------------------------------------------------
@app.get("/driver/{plate}")
def get_driver(plate: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM vozac WHERE tablica = ?", (plate,))
    driver = cursor.fetchone()
    conn.close()

    if driver:
        return {
            "vozac_id": driver[0],
            "ime": driver[1],
            "tablica": driver[2],
            "auto_tip": driver[3],
            "invalid": bool(driver[4]),
            "rezervacija": bool(driver[5])
        }

    return {"error": "Driver not found"}
#-------------------------------------------------------------------------
# --------------------------------------------------------
# ADD DRIVER
# --------------------------------------------------------
class Vozac(BaseModel):
    ime: str
    tablica: str
    auto_tip: str
    invalid: bool = False
    rezervacija: bool = False

@app.post("/add_driver")
def add_driver(driver: Vozac):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO vozac (ime, tablica, auto_tip, invalid, rezervacija)
        VALUES (?, ?, ?, ?, ?)
    """, (driver.ime, driver.tablica, driver.auto_tip, int(driver.invalid), int(driver.rezervacija)))
    conn.commit()
    conn.close()
    return {"message": "Vozac uspješno dodan."}


# --------------------------------------------------------
# ADD VIOLATION TYPE
# --------------------------------------------------------
class Prekrsaj(BaseModel):
    opis: str
    kazna: int

@app.post("/add_violation_type")
def add_violation_type(v: Prekrsaj):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO prekrsaji (opis, kazna)
        VALUES (?, ?)
    """, (v.opis, v.kazna))
    conn.commit()
    conn.close()
    return {"message": "Prekrsaj dodan."}


# --------------------------------------------------------
# NEW: GET ALL DRIVERS
# --------------------------------------------------------
@app.get("/vozaci")
def list_vozaci():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vozac")
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "vozac_id": r[0],
            "ime": r[1],
            "tablica": r[2],
            "auto_tip": r[3],
            "invalid": bool(r[4]),
            "rezervacija": bool(r[5])
        }
        for r in rows
    ]


# --------------------------------------------------------
# NEW: GET ALL VIOLATIONS
# --------------------------------------------------------
@app.get("/prekrsaji")
def list_prekrsaji():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT prekrsaj_id, opis, kazna FROM prekrsaji")
    rows = cursor.fetchall()
    conn.close()

    return [
        {"prekrsaj_id": r[0], "opis": r[1], "kazna": r[2]}
        for r in rows
    ]


# --------------------------------------------------------
# RECORD CONFIRMED VIOLATION
# --------------------------------------------------------
class Detektovano(BaseModel):
    vozac_id: int
    prekrsaj_id: int
    slika1: str
    slika2: str | None = None

@app.post("/record_violation")
def record_violation(d: Detektovano):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO detektovano (vozac_id, prekrsaj_id, vrijeme, slika1, slika2)
        VALUES (?, ?, ?, ?, ?)
    """, (d.vozac_id, d.prekrsaj_id, timestamp, d.slika1, d.slika2))
    conn.commit()
    conn.close()
    return {"message": "Prekršaj evidentiran."}

@app.post("/reject_violation")
def reject_violation(d: Detektovano):
    """
    Kopiraj slike u rejected folder za buduće treniranje modela
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Kopiraj prvu sliku
    if d.slika1 and os.path.exists(d.slika1):
        new_name = f"rejected_first_{timestamp}.jpg"
        shutil.copy(d.slika1, os.path.join(REJECTED_DIR, "first", new_name))

    # Kopiraj zoom sliku
    if d.slika2 and os.path.exists(d.slika2):
        new_name = f"rejected_zoom_{timestamp}.jpg"
        shutil.copy(d.slika2, os.path.join(REJECTED_DIR, "zoom", new_name))

    return {"message": "Odbijene slike sačuvane za treniranje."}

# --------------------------------------------------------
# FIRST IMAGE – CHECK VIOLATION
# --------------------------------------------------------
@app.post("/analyze_first_image")
async def analyze_first_image(file: UploadFile = File(...)):
    first_path = os.path.join(UPLOAD_DIR, "first_image.jpg")
    with open(first_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    results = model(first_path)
    boxes = results[0].boxes
    class_names = model.names

    detected_violation_class = None

    for box in boxes:
        cls_name = class_names[int(box.cls[0])]
        if cls_name.startswith("NepropisnoParkirano"):
            detected_violation_class = cls_name
            break

    if detected_violation_class is None:
        return {"status": "OK", "message": "Nema prekršaja — pravilno parkirano."}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT prekrsaj_id FROM prekrsaji WHERE opis = ?", (detected_violation_class,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {"status": "OK", "message": "Nema prekršaja — klasa nije u bazi."}

    return {
        "status": "NEEDS_ZOOM",
        "prekrsaj_id": row[0],
        "detected_violation": detected_violation_class,
        "message": "Približi se da očitamo tablicu."
    }


# --------------------------------------------------------
# ZOOM IMAGE – READ PLATE + DRIVER + RETURN FULL VIOLATION
# --------------------------------------------------------
@app.post("/analyze_zoom_image")
async def analyze_zoom_image(
    file: UploadFile = File(...),
    prekrsaj_id: int = Form(...)
):
    zoom_path = os.path.join(UPLOAD_DIR, "zoom_image.jpg")
    with open(zoom_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    results = model(zoom_path)
    boxes = results[0].boxes

    plate_box = None
    for box in boxes:
        cls_name = model.names[int(box.cls[0])].lower()
        if cls_name == "tablica":
            plate_box = box.xyxy[0].tolist()
            break

    if not plate_box:
        return {"status": "NO_PLATE"}

    crop_path = crop_plate(zoom_path, plate_box)
    plate_text = read_plate(crop_path) or "Unknown"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM vozac WHERE tablica = ?", (plate_text,))
    driver = cursor.fetchone()

    cursor.execute("SELECT opis, kazna FROM prekrsaji WHERE prekrsaj_id = ?", (prekrsaj_id,))
    prek = cursor.fetchone()

    conn.close()

    if not driver:
        return {"status": "NO_DRIVER", "plate": plate_text}

    return {
        "status": "READY_TO_CONFIRM",
        "plate": plate_text,
        "vozac": {
            "vozac_id": driver[0],
            "ime": driver[1],
            "tablica": driver[2],
            "auto_tip": driver[3],
            "invalid": bool(driver[4]),
            "rezervacija": bool(driver[5])
        },
        "prekrsaj_opis": prek[0],
        "prekrsaj_kazna": prek[1],
        "prekrsaj_id": prekrsaj_id,
        "slika1": os.path.join(UPLOAD_DIR, "first_image.jpg"),
        "slika2": zoom_path,
    }


# --------------------------------------------------------
# RUN SERVER
# --------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)