import os
import json
import logging
from flask import Flask, render_template, request, redirect, url_for, send_file
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from google.cloud import storage
import qrcode
import io
from datetime import datetime
import uuid
from google.cloud import secretmanager

# Configuración de logging
logging.basicConfig(level=logging.INFO)

# Inicializar la aplicación Flask
app = Flask(__name__)

# Función para cargar el secreto desde Google Secret Manager
def load_secret(secret_name):
    client = secretmanager.SecretManagerServiceClient()
    secret = client.access_secret_version(request={"name": secret_name}).payload.data.decode("UTF-8")
    return json.loads(secret)

# Cargar configuraciones desde el secreto
env = load_secret("projects/970772571927/secrets/qr-generator-secrets/versions/latest")
db_user = env.get("db_user")
db_pass = env.get("db_pass")
bucket_name = env.get("bucket_name")  # Nombre del bucket
mongo_uri = env.get("mongo_uri")
project_id = env.get("project_id")

# Validación de configuraciones
if not all([db_user, db_pass, bucket_name, mongo_uri, project_id]):
    raise ValueError("Faltan claves en la configuración del secreto.")

# Configuración de MongoDB
mongo_client = MongoClient(mongo_uri)
db = mongo_client['BaseNueva']
collection_qr = db['qr_codes']

# Cliente de GCS
storage_client = storage.Client(project=project_id)

# Función para generar códigos QR
def generar_qr(dato):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(dato)
    qr.make(fit=True)
    return qr.make_image(fill="black", back_color="white")

# Función para subir imágenes a GCS sin generar una URL pública
def upload_qr_to_gcs(qr_image, blob_name):
    try:
        bucket = storage_client.bucket(bucket_name)
        image_bytes = io.BytesIO()
        qr_image.save(image_bytes, format="PNG")
        image_bytes.seek(0)
        blob = bucket.blob(f"{blob_name}")
        blob.upload_from_file(image_bytes, content_type="image/png")
        return blob.name
    except Exception as e:
        logging.error(f"Error al subir QR a GCS: {e}")
        return None

# Endpoint para servir la imagen desde GCS
@app.route('/get_qr/<blob_name>')
def get_qr(blob_name):
    try:
        logging.info(f"Blob name recibido: {blob_name}")  # Log para depuración
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        if not blob.exists():
            logging.error(f"El archivo {blob_name} no existe en el bucket.")
            return "Imagen no encontrada.", 404

        image_bytes = io.BytesIO()
        blob.download_to_file(image_bytes)
        image_bytes.seek(0)

        return send_file(image_bytes, mimetype='image/png')
    except Exception as e:
        logging.error(f"Error al obtener el QR desde GCS: {e}")
        return "Error al obtener la imagen.", 500

# Página principal
@app.route('/')
def index():
    try:
        mongo_client.admin.command('ping')
        historial = list(collection_qr.find({}, {"_id": 0}).sort("created_at", -1))
        db_status = "Conexión exitosa"
        db_status_class = "success"
    except ServerSelectionTimeoutError:
        db_status = "No se puede conectar a MongoDB"
        db_status_class = "error"
        historial = []
    except Exception as e:
        logging.error(f"Error al recuperar el historial: {e}")
        historial = []
        db_status = "Error inesperado"
        db_status_class = "error"

    return render_template('index.html', historial=historial, db_status=db_status, db_status_class=db_status_class)

# Generación de código QR
@app.route('/generar', methods=['POST'])
def generar_codigo_qr():
    dato = request.form.get('dato', '').strip()
    if not dato:
        return redirect(url_for('index'))

    unique_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    blob_name = f"qr_codes/{timestamp}_{unique_id}.png"

    try:
        qr_image = generar_qr(dato)
        blob_name_in_gcs = upload_qr_to_gcs(qr_image, blob_name)

        if blob_name_in_gcs:
            collection_qr.insert_one({
                'dato': dato,
                'filename': blob_name,  # blob_name ya incluye "qr_codes/"
                'created_at': datetime.now()
            })

        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Error al generar el QR: {e}")
        return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
