import os
import json
from fastapi import UploadFile

async def save_uploaded_file(file: UploadFile, upload_folder: str):
    filename = file.filename
    file_path = os.path.join(upload_folder, filename)
    os.makedirs(upload_folder, exist_ok=True) # Ensure upload folder exists
    file_content = await file.read() # Read the content asynchronously
    with open(file_path, "wb") as buffer: # Use synchronous open
        buffer.write(file_content) # Write content synchronously
    return file_path

def read_output_file(output_file: str):
    if not os.path.exists(output_file):
        return None
    with open(output_file, 'r') as f:
        output_data = f.read()
    return json.loads(output_data)
