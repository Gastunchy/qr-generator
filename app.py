import os
import uuid
import json
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from google.cloud import secretmanager, storage
import qrcode
import io

# Configuración de logging
logging.basicConfig(level=logging.INFO)

# Inicializar la aplicación Flask
app = Flask(__name__)

# Función para acceder a Secret Manager
def access_secret_version(secret_id, version_id="latest"):
    try:
        client = secretmanager.SecretManagerServiceClient()
        project_id = os.getenv("GCP_PROJECT_ID")

        if not project_id:
            raise ValueError("El ID del proyecto no está configurado en las variables de entorno.")

        secret_path = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
        response = client.access_secret_version(name=secret_path)
        return json.loads(response.payload.data.decode("UTF-8"))
    except Exception as e:
        logging.error(f"Error al acceder a Secret Manager: {e}")
        raise

# Cargar credenciales desde Secret Manager
try:
    secrets_data = access_secret_version("qr-generator-secret")
    db_user = secrets_data['db_user']
    db_pass = secrets_data['db_pass']
    twilio_config = {
        'sid': secrets_data['Twilio_sid'],
        'token': secrets_data['Twilio_token'],
        'service_sid': secrets_data['Twilio_service_sid'],
        'destino': secrets_data['Twilio_destino'],
    }
    gcs_config = {
        'bucket_name': secrets_data['GCS_BUCKET_NAME'],
        'project_id': os.getenv("GCP_PROJECT_ID"),
    }
except KeyError as e:
    logging.error(f"Falta la clave esperada en el secreto: {e}")
    raise
except Exception as e:
    logging.error(f"No se pudieron cargar las credenciales: {e}")
    raise

# Configuración de MongoDB
mongo_uri = f"mongodb+srv://{db_user}:{db_pass}@basenueva.hxpdn.mongodb.net/?retryWrites=true&w=majority"
client = MongoClient(mongo_uri)
db = client['BaseNueva']
collection_qr = db['qr_codes']

# Cliente GCS
storage_client = storage.Client(project=gcs_config['project_id'])

# Función para subir imágenes a GCS
def upload_qr_to_gcs(qr_image, blob_name):
    try:
        bucket = storage_client.bucket(gcs_config['bucket_name'])

        # Convertir la imagen QR a un archivo en memoria
        image_bytes = io.BytesIO()
        qr_image.save(image_bytes, format="PNG")
        image_bytes.seek(0)

        # Subir la imagen a GCS
        blob = bucket.blob(blob_name)
        blob.upload_from_file(image_bytes, content_type="image/png")
        return blob.public_url
    except Exception as e:
        logging.error(f"Error al subir QR a GCS: {e}")
        return None

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

@app.route('/')
def index():
    try:
        client.admin.command('ping')  # Verificar conexión con la base de datos
        historial = list(collection_qr.find({}, {"_id": 0}).sort("created_at", -1))
        db_status = "Conexión exitosa"
        db_status_class = "success"
    except ServerSelectionTimeoutError:
        db_status = "Conexión fallida"
        db_status_class = "error"
        historial = []
    except Exception as e:
        logging.error(f"Error al recuperar el historial: {e}")
        historial = []
        db_status = "Error inesperado"
        db_status_class = "error"

    return render_template('index.html', historial=historial, db_status=db_status, db_status_class=db_status_class)

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
        public_url = upload_qr_to_gcs(qr_image, blob_name)

        if public_url:
            collection_qr.insert_one({
                'dato': dato,
                'filename': public_url,
                'created_at': datetime.now()
            })

        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Error al generar el QR: {e}")
        return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
