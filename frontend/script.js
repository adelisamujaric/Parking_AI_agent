// URL backend API-ja – koristićemo ga kasnije kad FastAPI bude gotov
const API_URL = "http://127.0.0.1:8000/analyze-image";

let state = "analyze"; 
// analyze -> prvi klik
// zoom    -> drugi klik (“Priđi bliže”)


// --------------------------------------------------------
// OTVARANJE FILE PICKER-a
// --------------------------------------------------------
function triggerUpload() {
    document.getElementById("imageInput").click();
}


// --------------------------------------------------------
// PRIKAZ PREVIEW SLIKE (lijevi okvir)
// --------------------------------------------------------
document.getElementById("imageInput").addEventListener("change", function () {
    let file = this.files[0];
    if (!file) return;

    let img = document.getElementById("previewImage");
    img.src = URL.createObjectURL(file);
    img.style.display = "block";
});


// --------------------------------------------------------
// GLAVNA LOGIKA dugmeta: ANALIZIRAJ → PRIĐI BLIŽE
// --------------------------------------------------------
async function analyzeOrZoom() {
    let btn = document.getElementById("actionButton");
    let fileInput = document.getElementById("imageInput");
    let file = fileInput.files[0];

    let textResultBox = document.getElementById("resultsText");

    if (!file) {
        alert("Prvo odaberi sliku!");
        return;
    }

    // ----------------------------------------------------
    // 🌟 1) ANALIZA PRVE SLIKE
    // ----------------------------------------------------
    if (state === "analyze") {

        textResultBox.innerHTML = "<p><b>Analiza u toku...</b></p>";

        const formData = new FormData();
        formData.append("file", file);

        let data;

        try {
            const res = await fetch(API_URL, {
                method: "POST",
                body: formData
            });

            data = await res.json();

        } catch (e) {
            console.error(e);
            textResultBox.innerHTML = "<p style='color:red'><b>Greška pri analizi (backend vjerovatno nije pokrenut).</b></p>";
            return;
        }

        // Prikaži PRVU detekciju u lijevom okviru
        showFirstDetection(data, file);

        // Prikaži tekstualne rezultate ispod okvira
        textResultBox.innerHTML = formatTextResults(data);

        // Promijeni dugme u “Priđi bliže”
        btn.textContent = "🔍 Priđi bliže";
        btn.style.background = "#ff9600";
        state = "zoom";
    }


    // ----------------------------------------------------
    // 🌟 2) PRIĐI BLIŽE (druga slika)
    // ----------------------------------------------------
    else if (state === "zoom") {

        alert("Odaberi novu, bližu sliku za detaljniju analizu.");

        // Očisti stari file
        fileInput.value = "";
        document.getElementById("previewImage").style.display = "none";

        // Promijeni dugme nazad u “Analiziraj”
        btn.textContent = "🔍 Analiziraj";
        btn.style.background = "#00a86b";
        state = "analyze";

        // Otvori file picker odmah
        triggerUpload();
    }
}



// ------------------------------------------------------------
//  PRVA DETEKCIJA (img + bounding boxovi u canvas1)
// ------------------------------------------------------------
function showFirstDetection(data, file) {
    let img = document.getElementById("firstImage");
    img.src = URL.createObjectURL(file);
    img.style.display = "block";

    drawBoundingBoxes("canvas1", img, data);
}


// ------------------------------------------------------------
//  DRUGA DETEKCIJA (img + bounding boxovi u canvas2)
// ------------------------------------------------------------
function showSecondDetection(data, file) {
    let img = document.getElementById("secondImage");
    img.src = URL.createObjectURL(file);
    img.style.display = "block";

    drawBoundingBoxes("canvas2", img, data);
}



// ------------------------------------------------------------
//  FUNKCIJA: ISCRTAVANJE BOUNDING BOXOVA
// ------------------------------------------------------------
function drawBoundingBoxes(canvasId, img, data) {
    let canvas = document.getElementById(canvasId);
    let ctx = canvas.getContext("2d");

    canvas.width = img.width;
    canvas.height = img.height;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!data.detections) return;

    data.detections.forEach(det => {
        let [x1, y1, x2, y2] = det.box;

        ctx.strokeStyle = "red";
        ctx.lineWidth = 2;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

        ctx.fillStyle = "red";
        ctx.font = "14px Arial";
        ctx.fillText(det.class, x1, y1 - 5);
    });
}



// ------------------------------------------------------------
//  FORMATIRANJE TEKSTUALNIH REZULTATA
// ------------------------------------------------------------
function formatTextResults(data) {
    let html = "<p><b>Detekcije:</b></p>";

    if (data.detections && data.detections.length > 0) {
        data.detections.forEach(det => {
            html += `<p>${det.class} (${(det.confidence * 100).toFixed(1)}%)</p>`;
        });
    } else {
        html += "<p>Nema detekcija.</p>";
    }

    if (data.violation) {
        html += `<p style="color:red;"><b>Prekršaj: ${data.violation}</b></p>`;
    }

    return html;
}
