from ultralytics import YOLO
import easyocr
import cv2

# 1️⃣ Test YOLO modela
print("🔍 Učitavam YOLO model...")
model = YOLO("yolov8n.pt")  # osnovni model
print("✅ YOLO model učitan!")

# 2️⃣ Test EasyOCR
print("🔤 Pokrećem OCR...")
reader = easyocr.Reader(['en'])
print("✅ EasyOCR spreman!")

# 3️⃣ Test OpenCV
print("📸 Testiram OpenCV...")
img = cv2.imread("test.jpg")
if img is None:
    print("⚠️ Nema slike 'test.jpg' u projektu, ali OpenCV radi.")
else:
    print("✅ OpenCV učitao sliku.")

print("🎉 Sve radi! Spremna si za rad sa AI agentom.")
