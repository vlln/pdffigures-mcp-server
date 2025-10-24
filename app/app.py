import os
import logging
import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from typing import Annotated, Union
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from utils import save_uploaded_file, read_output_file
from service import process_pdf_extraction

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# Configuration
UPLOAD_FOLDER = "/app/uploads/"
OUTPUT_FOLDER = os.getenv("OUTPUT_FOLDER", "/app/output/")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

RESOURCE_BASE_URL = os.getenv("RESOURCE_BASE_URL")
SERVER_PORT = os.getenv("SERVER_PORT", "5001")

mcp = FastMCP("PDF Extraction Service")

INTERNAL_API_BASE_URL = os.getenv("INTERNAL_API_URL", f"http://localhost:{SERVER_PORT}")
INTERNAL_API_EXTRACT_ENDPOINT = f"{INTERNAL_API_BASE_URL}/api/extract"

# Helper function to convert local path to full URL
def construct_full_urls(data: dict, base_url: str) -> dict:
    if not base_url:
        return data

    base_url = base_url.rstrip('/') + '/resources/'

    for figure_data in data:
        figure_data['renderURL'] = base_url + figure_data['renderURL'].split('/')[-1]

    return data

@mcp.tool
async def extract_figures_from_pdf(pdf_url: str) -> dict:
    """
    Extracts figures and tables from a PDF file provided via URL.

    This tool processes a PDF from the given URL using PDFFigures 2.0 model,
    extracts all figures and tables, and returns a JSON object containing 
    the extracted data including metadata, captions, bounding boxes, and 
    URLs to rendered images.

    Args:
        pdf_url: The URL of a publicly accessible PDF file to be processed.

    Returns:
        Dictionary containing extracted figures and tables data.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            form_data = {"pdf_url": pdf_url}
            response = await client.post(
                INTERNAL_API_EXTRACT_ENDPOINT,
                data=form_data
            )
            
            response.raise_for_status()
            return {"result": response.json()}
            
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
        return {
            "error": f"API request failed with status {e.response.status_code}",
            "detail": e.response.text
        }
    except httpx.RequestError as e:
        logging.error(f"Request error occurred: {str(e)}")
        return {
            "error": f"Failed to connect to extraction service: {str(e)}"
        }
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return {
            "error": f"Unexpected error during PDF extraction: {str(e)}"
        }

mcp_app = mcp.http_app(path="/")

app = FastAPI(title="PDF Extraction API Server", lifespan=mcp_app.lifespan, redirect_slashes=False)
app.mount("/mcp", mcp_app)

app.mount("/resources", StaticFiles(directory=OUTPUT_FOLDER), name="resources")

api_router = APIRouter(prefix="/api")

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "pdf-extraction-server"}

@api_router.post("/extract")
async def extract_figures(
    file: Annotated[Union[UploadFile, None], File(description="A PDF file to be processed.")] = None,
    pdf_url: Annotated[Union[str, None], Form(description="The URL of a PDF file to be processed.")] = None
):
    """
    Extracts figures and tables from a given PDF file.
    """

    if file and pdf_url:
        logging.error("Cannot provide both 'file' and 'pdf_url'.")
        raise HTTPException(status_code=400, detail="Cannot provide both 'file' and 'pdf_url'.")

    file_path_to_process = None
    original_filename_to_process = None

    if pdf_url:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(pdf_url)
                response.raise_for_status()
                original_filename_to_process = os.path.basename(pdf_url)
                if not original_filename_to_process.lower().endswith(".pdf"):
                    original_filename_to_process += ".pdf"
                file_path_to_process = os.path.join(UPLOAD_FOLDER, original_filename_to_process)
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                with open(file_path_to_process, "wb") as buffer:
                    buffer.write(response.content)
        except httpx.RequestError as e:
            logging.error(f"Failed to download PDF from URL: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to download PDF from URL: {e}")
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to download PDF from URL: {e.response.status_code} - {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to download PDF from URL: {e}")
    elif file: # file upload
        if not file.filename:
            logging.error("No selected file")
            raise HTTPException(status_code=400, detail="No selected file")
        original_filename_to_process = file.filename
        file_path_to_process = await save_uploaded_file(file, UPLOAD_FOLDER)
    else:
        logging.error("Either 'file' or 'pdf_url' must be provided.")
        raise HTTPException(status_code=400, detail="Either 'file' or 'pdf_url' must be provided.")

    if not original_filename_to_process.lower().endswith('.pdf'):
        logging.error("Only PDF files are allowed.")
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    response_content = process_pdf_extraction(
        file_path=file_path_to_process,
        original_filename=original_filename_to_process,
        output_dir=OUTPUT_FOLDER
    )

    if "error" in response_content:
        logging.error(f"Error during PDF extraction: {response_content['error']}")
        raise HTTPException(status_code=500, detail=response_content["error"])
    
    final_response = construct_full_urls(response_content, str(RESOURCE_BASE_URL))
    
    return JSONResponse(content=final_response, status_code=200)

app.include_router(api_router)

@app.get("/")
async def root():
    return {
        "message": "PDF Extraction Server", 
        "api_docs": "/docs",
        "api_base": "/api",
        "mcp_endpoint": "/mcp"
    }