import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from pymongo import MongoClient
from google.cloud import storage
import qrcode
import io
import logging

# Configuración de Flask
app = Flask(__name__)

# Leer los secretos desde las variables de entorno
db_user = os.getenv('DB_USER')  # Configurado en Cloud Run como secreto
db_pass = os.getenv('DB_PASS')  # Configurado en Cloud Run como secreto
bucket_name = os.getenv('BUCKET_NAME')  # Configurado en Cloud Run como secreto
mongo_uri = os.getenv('MONGO_URI')  # Configurado en Cloud Run como secreto
project_id = os.getenv('PROJECT_ID', 'calm-segment-443101-a8')

# Verificar que todos los secretos se cargaron correctamente
if not all([db_user, db_pass, bucket_name, mongo_uri, project_id]):
    logging.error("Faltan algunos secretos. Verifica la configuración en Secret Manager.")

# Conexión a MongoDB
client = MongoClient(mongo_uri)
db = client['qr_database']
collection_qr = db['qr_codes']

# Función para subir a Google Cloud Storage
def upload_qr_to_gcs(qr_image, blob_name):
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    image_bytes = io.BytesIO()
    qr_image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    blob = bucket.blob(blob_name)
    blob.upload_from_file(image_bytes, content_type='image/png')
    logging.info(f"Archivo subido a GCS: {blob_name}")
    return blob.public_url

@app.route('/')
def index():
    # Recuperar el historial de QR generado desde MongoDB
    historial_mongo = list(collection_qr.find({}, {"_id": 0}).sort("created_at", -1))
    return render_template('index.html', historial=historial_mongo)

@app.route('/generar', methods=['POST'])
def generar_codigo_qr():
    dato = request.form['dato']

    if not dato.strip():
        return redirect(url_for('index'))

    # Generar un nombre único para el archivo QR
    unique_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    blob_name = f"qr_codes/{timestamp}_{unique_id}.png"

    # Generar código QR
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(dato)
    qr.make(fit=True)
    qr_image = qr.make_image(fill='black', back_color='white')

    # Subir QR a GCS
    public_url = upload_qr_to_gcs(qr_image, blob_name)

    # Guardar en MongoDB
    if public_url:
        collection_qr.insert_one({
            'dato': dato,
            'filename': public_url,
            'created_at': datetime.now()
        })

    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
