import subprocess
import os
import logging
import time
from pathlib import Path
from utils import read_output_file

logger = logging.getLogger(__name__)

def process_pdf_extraction(file_path: str, original_filename: str, output_dir: str) -> dict:
    """
    Processes a PDF file to extract figures and tables using pdffigures2.

    Args:
        file_path: The absolute path to the PDF file.
        original_filename: The original name of the PDF file.
        output_dir: The directory where output files will be stored.

    Returns:
        A dictionary containing the extracted figures and tables metadata,
        or a structured error dictionary if the extraction fails.
    """
    java_opts = os.getenv('JAVA_OPTS', '-XX:MaxRAMPercentage=75.0')
    jar_path = os.getenv('PDFFIGURES_JAR_PATH', '/pdffigures2/pdffigures2.jar')
    work_dir = os.getenv('PDFFIGURES_WORK_DIR', '/pdffigures2')

    # Ensure the output directory exists.
    os.makedirs(output_dir, exist_ok=True)

    # FIX: The pdffigures2 JAR concatenates the prefix directly with the
    # filename. The prefix MUST end with a path separator to be treated as a directory.
    # os.path.join(output_dir, '') is a robust way to ensure this.
    output_prefix = os.path.join(output_dir, '')

    base_command = [
        'java',
        java_opts,
        '-Dsun.java2d.cmm=sun.java2d.cmm.kcms.KcmsServiceProvider',
        '-jar',
        jar_path,
        file_path,
        "-m", output_prefix,
        "-d", output_prefix,
        "--dpi", "300"
    ]

    start_time = time.time()
    logger.debug(f"Running pdffigures2 on {file_path}")
    logger.debug(f"Executing command: {' '.join(base_command)}")

    try:
        result = subprocess.run(
            base_command,
            capture_output=True,
            text=True,
            check=False,  # We will check the returncode manually for better error reporting.
            cwd=work_dir,
            timeout=180 # Add a timeout to prevent stalled processes.
        )
    except subprocess.TimeoutExpired as e:
        logger.error(f"pdffigures2 process timed out after 180 seconds for file: {file_path}")
        logger.error(f"STDOUT: {e.stdout}")
        logger.error(f"STDERR: {e.stderr}")
        return {
            "error": "PDF processing timed out.",
            "detail": "The extraction process took too long to complete."
        }


    if result.returncode != 0:
        # This block will now execute upon failure, making logs visible.
        error_message = (
            f"pdffigures2 failed with exit code {result.returncode} "
            f"for file '{original_filename}'."
        )
        logger.error(error_message)
        logger.error(f"STDOUT: {result.stdout}")
        logger.error(f"STDERR: {result.stderr}")
        return {
            "error": "PDF processing failed.",
            "detail": {
                "message": error_message,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        }

    end_time = time.time()
    processing_time = int((end_time - start_time) * 1000)
    logger.debug(f"Processing time: {processing_time} ms for {original_filename}")

    # Use pathlib for more robust path handling.
    base_filename = Path(original_filename).stem
    metadata_filename = f"{base_filename}.json"
    metadata_path = Path(output_dir) / metadata_filename

    logger.debug(f"Attempting to read metadata file: {metadata_path}")

    if not metadata_path.is_file():
        # This is a secondary check in case the process succeeded (exit 0) but
        # inexplicably failed to create the output file.
        error_msg = f"Output file not found after successful process execution: {metadata_path}"
        logger.error(error_msg)
        return {"error": error_msg}

    figures_data = read_output_file(str(metadata_path))
    # read_output_file should handle the case where the file is empty or invalid JSON.
    if figures_data is None:
        error_msg = f"Failed to read or parse metadata file: {metadata_path}"
        logger.error(error_msg)
        return {"error": error_msg}

    return figures_data