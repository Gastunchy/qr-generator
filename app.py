import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from pymongo import MongoClient
from google.cloud import storage, secretmanager
import qrcode
import io
import json
import logging
import os
import logging
import json
from google.cloud import secretmanager

def access_secret_version(secret_id, version_id="latest"):
    """Accede al secreto desde Google Secret Manager."""
    try:
        # Cliente de Secret Manager
        client = secretmanager.SecretManagerServiceClient()
        
        # Obtener el ID del proyecto desde la variable de entorno o definir uno predeterminado
        project_id = os.getenv('PROJECT_ID', 'calm-segment-443101-a8')  # Define esto en tu entorno
        if not project_id:
            raise ValueError("PROJECT_ID no está definido en las variables de entorno.")
        
        # Ruta del secreto
        name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
        
        # Acceso al secreto
        response = client.access_secret_version(request={"name": name})
        secret_data = response.payload.data.decode('UTF-8')
        
        # Retorna el secreto como un diccionario
        logging.info(f"Secreto {secret_id} recuperado exitosamente.")
        return json.loads(secret_data)
    
    except Exception as e:
        logging.error(f"Error al acceder al secreto {secret_id}: {e}")
        return {}

# Acceder a secretos desde Secret Manager
secret_id = "qr-generator-secrets"  # Nombre del secreto creado en GCP
secrets_data = access_secret_version(secret_id)

# Variables de configuración
db_user = secrets_data.get("db_user", "")
db_pass = secrets_data.get("db_pass", "")
bucket_name = secrets_data.get("bucket_name", "")
mongo_uri = secrets_data.get("mongo_uri", "")
project_id = secrets_data.get("project_id", "")

# Verificar que todos los secretos se cargaron correctamente
if not all([db_user, db_pass, bucket_name, mongo_uri, project_id]):
    logging.error("Faltan algunos secretos. Verifica la configuración en Secret Manager.")

# Configuración de Flask
app = Flask(__name__)

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
