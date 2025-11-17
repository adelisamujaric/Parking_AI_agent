import sqlite3
from datetime import datetime

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import shutil
from backend.database import init_db,  DB_PATH
from pydantic import BaseModel
from backend.ocr import read_plate
from backend.utils import crop_plate
from ultralytics import YOLO
import cv2
import numpy as np

app = FastAPI()
init_db()

# CORS → da frontend može komunicirati
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Učitaj AI model (promijeni ako treba)
model = YOLO("backend/weights/best.pt")

#APIs
@app.get("/")
def root():
    return {"message": "AI Parking Agent backend is running!"}

@app.post("/detect")
async def detect_image(file: UploadFile = File(...)):
    # privremeno snimi upload sliku
    temp_path = "backend/temp_image.jpg"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # detekcija YOLO
    results = model(temp_path)
    detections = results[0].boxes.data.tolist()

    return {"detections": detections}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

@app.post("/detect_plate")
async def detect_plate(file: UploadFile = File(...)):
    # snimi originalnu sliku
    temp_path = "backend/temp_plate_source.jpg"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 1. YOLO detekcija samo tablice
    results = model(temp_path)
    boxes = results[0].boxes

    # Pronađi klasu 'Tablica' (mora se zvati isto kao tvoja klasa!)
    plate_box = None

    for box in boxes:
        cls_id = int(box.cls[0])
        cls_name = model.names[cls_id]

        if cls_name.lower() == "tablica":
            # xyxy format
            plate_box = box.xyxy[0].tolist()
            break

    if plate_box is None:
        return {"plate": None, "error": "Plate not detected"}

    # 2. Crop tablice
    crop_path = crop_plate(temp_path, plate_box)

    if crop_path is None:
        return {"plate": None, "error": "Crop failed"}

    # 3. OCR
    plate_text = read_plate(crop_path)

    return {
        "plate": plate_text if plate_text else "Not detected",
        "bbox": plate_box
    }

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

@app.post("/analyze_first_image")
async def analyze_first_image(file: UploadFile = File(...)):
    # 1️⃣ Snimi prvu sliku
    first_path = "backend/first_image.jpg"
    with open(first_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 2️⃣ YOLO detekcija
    results = model(first_path)
    boxes = results[0].boxes
    class_names = model.names

    detected_violation_class = None

    # 3️⃣ Provjeri da li YOLO vidi neki prekršaj
    for box in boxes:
        cls_id = int(box.cls[0])
        cls_name = class_names[cls_id]

        # Prekršaji počinju sa "Nepropisno"
        if cls_name.startswith("NepropisnoParkirano"):
            detected_violation_class = cls_name
            break

    # 4️⃣ Ako YOLO NIJE detektovao prekršaj → sve ok
    if detected_violation_class is None:
        return {
            "status": "OK",
            "message": "Nema prekršaja — pravilno parkirano."
        }

    # 5️⃣ Ako YOLO jeste detektovao prekršaj → provjeri da li postoji u bazi
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT prekrsaj_id FROM prekrsaji WHERE opis = ?",
        (detected_violation_class,)
    )
    row = cursor.fetchone()
    conn.close()

    # 6️⃣ YOLO je detektovao prekršaj koji NE postoji u bazi → nije pravi prekršaj
    if not row:
        return {
            "status": "OK",
            "message": "Nema prekršaja — YOLO detektovao nepoznatu klasu."
        }

    prekrsaj_id = row[0]

    # 7️⃣ Detektovan prekršaj → treba druga slika (zoom)
    return {
        "status": "NEEDS_ZOOM",
        "prekrsaj_id": prekrsaj_id,
        "detected_violation": detected_violation_class,
        "message": "Približi se da očitamo tablicu."
    }

@app.post("/analyze_zoom_image")
async def analyze_zoom_image(file: UploadFile = File(...), prekrsaj_id: int = 0):
    zoom_path = "backend/zoom_image.jpg"
    with open(zoom_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # YOLO detekcija tablice
    results = model(zoom_path)
    boxes = results[0].boxes

    plate_box = None
    for box in boxes:
        cls_id = int(box.cls[0])
        cls_name = model.names[cls_id]
        if cls_name.lower() == "tablica":
            plate_box = box.xyxy[0].tolist()
            break

    if not plate_box:
        return {"status": "NO_PLATE", "message": "Tablica nije pronađena."}

    # Crop teh
    crop_path = crop_plate(zoom_path, plate_box)
    plate_text = read_plate(crop_path) or "Unknown"

    # nađi vozača
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vozac WHERE tablica = ?", (plate_text,))
    driver = cursor.fetchone()
    conn.close()

    if not driver:
        return {
            "status": "NO_DRIVER",
            "plate": plate_text,
            "message": "Vozač nije pronađen u bazi."
        }

    return {
        "status": "READY_TO_CONFIRM",
        "plate": plate_text,
        "vozac": {
            "vozac_id": driver[0],
            "ime": driver[1],
            "tablica": driver[2],
            "auto_tip": driver[3],
            "invalid": bool(driver[4]),
            "rezervacija": bool(driver[5]),
        },
        "prekrsaj_id": prekrsaj_id,
        "slika1": "backend/first_image.jpg",
        "slika2": zoom_path
    }

class Confirm(BaseModel):
    vozac_id: int
    prekrsaj_id: int
    slika1: str
    slika2: str

@app.post("/confirm_violation")
def confirm_violation(c: Confirm):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO detektovano (vozac_id, prekrsaj_id, vrijeme, slika1, slika2)
        VALUES (?, ?, ?, ?, ?)
    """, (c.vozac_id, c.prekrsaj_id, timestamp, c.slika1, c.slika2))

    conn.commit()
    conn.close()

    return {"message": "Prekršaj uspješno snimljen!", "vrijeme": timestamp}
