import argparse
import os
import requests
import tempfile
import zipfile
import json
from urllib.parse import urljoin
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), 'output')

class FileDownloader:
    @staticmethod
    def download_file(download_url, output_path, timeout=30):
        """
        Downloads a file from the given URL to the specified path.
        
        :param download_url: The URL of the file to download.
        :param output_path: The file system path to save the downloaded file.
        :param timeout: The request timeout duration.
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            logging.debug(f"Starting download from {download_url}")
            response = requests.get(download_url, stream=True, timeout=timeout)
            response.raise_for_status()
            with open(output_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            logging.debug(f"Downloaded file to {output_path}")
        except requests.RequestException as e:
            logging.error(f"Failed to download {download_url}: {str(e)}")
            raise

    @staticmethod
    def download_extracted_data(response_data, output_dir, base_url="http://localhost:5001"):
        """
        Downloads metadata and figures from the extraction response data.

        :param response_data: The JSON response data from the server.
        :param output_dir: Directory to save the downloaded files.
        :param base_url: Base URL for downloading files.
        """
        try:
            logging.debug(f"download_extracted_data called with output_dir: {output_dir}")
            
            # Download Figures
            figures = [fig['renderURL'] for fig in response_data]
            logging.debug(f"Downloading figures: {figures}")
            for figure_download_url in figures:
                # Extract base filename to avoid absolute paths
                sanitized_figure_filename = os.path.basename(figure_download_url)
                logging.debug(f"Sanitized figure filename: {sanitized_figure_filename}")
                figure_output_path = os.path.join(output_dir, sanitized_figure_filename)
                logging.debug(f"Figure download URL: {figure_download_url}")
                logging.debug(f"Figure output path: {figure_output_path}")
                
                FileDownloader.download_file(figure_download_url, figure_output_path)

        except Exception as e:
            logging.error(f"Error downloading extracted data: {str(e)}")
            raise

class PDFExtractor:
    @staticmethod
    def extract_pdf(file_path, output_dir, url="http://localhost:5001/api/extract"):
        """
        Extracts figures and tables from a PDF file by sending it to the server and downloads the extracted data.

        :param file_path: Path to the PDF file to be extracted.
        :param output_dir: Directory to save the downloaded files.
        :param url: URL of the extraction service.
        :return: Response from the server.
        """
        try:
            logging.debug(f"Uploading {file_path} to {url} for extraction")
            logging.info(f"Extracting figures and tables from {file_path}")
            output_dir = DirectoryProcessor.setup_output_directory(output_dir)
            with open(file_path, 'rb') as file:
                files = {'file': file}
                response = requests.post(url, files=files)
            response.raise_for_status()
            logging.info(f"Extraction successful for {file_path}")
            response_data = response.json()
            logging.debug(f"Received response data: {json.dumps(response_data, indent=2)}")

            # normalize figures and tables paths with output directory

            FileDownloader.download_extracted_data(response_data, output_dir)

            logging.info(f"Downloading metadata for {file_path}")
            # Extract figure-level information
            return response_data
        
        except requests.RequestException as e:
            logging.error(f"Error extracting PDF: {str(e)}")
            raise


class DirectoryProcessor:
    @staticmethod
    def setup_output_directory(output_dir):
        """
        Ensures the output directory exists and is writable.

        :param output_dir: Path to the output directory.
        :return: Absolute path to the output directory.
        """
        try:
            output_dir = os.path.abspath(output_dir)
            logging.debug(f"Setting up output directory: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
            if not os.access(output_dir, os.W_OK):
                raise PermissionError(f"No write permission for directory: {output_dir}")
            logging.info(f"Output directory is set to {output_dir}")
            return output_dir
        except Exception as e:
            logging.error(f"Error setting up output directory: {str(e)}")
            raise

def get_figure_metadata(figure_metadata, fig):
    fig_filename = os.path.basename(fig)
    logging.debug(f"Searching for renderURL ending with: /{fig_filename}")
    render_url = next((item['renderURL'] for item in figure_metadata if 'renderURL' in item and item['renderURL'].endswith(f"/{fig_filename}")), None)
    if render_url:
        logging.debug(f"Found renderURL for {fig_filename}: {render_url}")
        figure_info = next((item for item in figure_metadata if item['renderURL'] == render_url), {})
        return figure_info
    else:
        logging.debug(f"No renderURL found for {fig_filename}")
        return {}
    
def extract_figures(input_path, output_dir, url=None): # TODO: Always return a list of dictionaries
    """
    Processes the given path and runs the extraction function.

    :param input_path: Path to the input file.
    :param output_dir: Directory to save the output files.
    :param url: URL for the extraction service (optional).
    """
    if os.path.isfile(input_path):
        if url is None:
            url = "http://localhost:5001/api/extract"
        response = PDFExtractor.extract_pdf(input_path, output_dir, url)
        logging.info(f"Extraction response/api/extract: {json.dumps(response, indent=2)}")

        # Log the type and content of response
        logging.debug(f"Type of response from extract_pdf: {type(response)}")
        logging.debug(f"Content of response from extract_pdf: {response}")

        # Check if response is a dictionary; if so, wrap it in a list
        if isinstance(response, dict):
            response_list = [response]
            logging.debug("Wrapped single response dictionary in a list.")
        elif isinstance(response, list):
            response_list = response
            logging.debug("Received a list of response dictionaries.")
        else:
            logging.error(f"Unexpected response type: {type(response)}")
            raise TypeError("extract_pdf should return a dictionary or a list of dictionaries.")

        # Further ensure each item in the list is a dictionary
        for idx, item in enumerate(response_list):
            if not isinstance(item, dict):
                logging.error(f"Item at index {idx} is not a dictionary: {type(item)}")
                raise TypeError("Each item in response_list should be a dictionary.")

        logging.debug(f"Returning response_list: {response_list}")
        return response_list
    else:
        logging.error("Invalid input path. It should be a file.")
        return None

def main():
    parser = argparse.ArgumentParser(description="Process PDF files and extract figures, tables and images.")
    parser.add_argument('input_path', help="Path to the input PDF file.")
    parser.add_argument('--output_dir', nargs='?', default='output', help="Directory to save extracted figures. Defaults to './output' if not specified.")
    parser.add_argument('--url', help="URL for the extraction service. e.g. 'http://localhost:5001/api/extract'. Only needed if you change the port while running Docker.")
    
    args = parser.parse_args()

    try:
        output_dir = args.output_dir if args.output_dir else DEFAULT_OUTPUT_DIR
        output_dir = DirectoryProcessor.setup_output_directory(output_dir)
        response = extract_figures(args.input_path, output_dir, args.url)
        print(json.dumps(response, indent=2))
    except Exception as e:
        logging.error(f"Error during extraction: {str(e)}")

if __name__ == "__main__":
    main()