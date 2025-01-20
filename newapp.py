from datetime import timedelta
import os
import uuid
import json
import PyPDF2
import logging
import base64
import io
import shutil
from PIL import Image
import fitz  # PyMuPDF for better image extraction
import openai
import flask
from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_session import Session
from dotenv import load_dotenv
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
)
 
 
 
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('app.log', encoding='utf-8')]
)
logger = logging.getLogger(__name__)
 
class PDFSearchApp:
    def __init__(self):
        # Load environment variables
        load_dotenv()
       
        # Validate and configure services
        self._validate_env_vars()
       
        # Flask App Configuration
        self.app = Flask(__name__)
        self._configure_flask_app()
       
        # Configure Azure Services
        self._configure_azure_services()
       
        # Ensure index exists
        self._ensure_index_exists()
       
        # Setup routes
        self._setup_routes()
       
        # # Register before_request handler to always start with fresh chat
        # @self.app.before_request
        # def ensure_fresh_chat():
        #     # Clear chat history for all new sessions and page loads
        #     if 'chat_history' in session:
        #         session.pop('chat_history')
        #     session['chat_history'] = []
 
    def _configure_flask_app(self):
        """Configure Flask application settings"""
        self.app.config['SECRET_KEY'] = os.urandom(24)
        self.app.config['SESSION_TYPE'] = 'filesystem'
        self.app.config['UPLOAD_FOLDER'] = 'uploads'
        self.app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
        self.app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)  # Session timeout
        os.makedirs(self.app.config['UPLOAD_FOLDER'], exist_ok=True)
        Session(self.app)
 
    def extract_pdf_content(self, file_path):
        """
        Extract both text and images from PDF
        Returns a dictionary with text and base64 encoded images
        """
        content = {
            'text': [],
            'images': []
        }
     
        try:
            # Use PyMuPDF for more robust extraction
            doc = fitz.open(file_path)
           
            for page_num in range(len(doc)):
                page = doc[page_num]
               
                # Extract text
                page_text = page.get_text()
             
                content['text'].append({
                    'page_num': page_num + 1,
                    'text': page_text
                   
                })
               
                # Extract images
                for img_index, img in enumerate(page.get_images(full=True)):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image['image']
                   
                    # Convert to PIL Image to ensure compatibility
                    pil_image = Image.open(io.BytesIO(image_bytes))
                   
                    # Convert to PNG for web compatibility
                    buffered = io.BytesIO()
                    pil_image.save(buffered, format="PNG")
                    img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                   
                    content['images'].append({
                        'page_num': page_num + 1,
                        'image_index': img_index,
                        'base64_image': img_base64
                    })
 
            doc.close()
            return content
 
        except Exception as e:
            logger.error(f"PDF content extraction error: {e}")
            return content
 
    def index_document_with_images(self, pdf_content, filename, blob_name):
        """
        Index document with text and incorporate image information
        """
        try:
            # Extract keywords (example method, you might want to implement a more sophisticated keyword extraction)
            keywords = ' '.join(self._extract_keywords(pdf_content['text']))
 
            # Track if document has any images
            document_has_images = bool(pdf_content['images'])
 
            # Combine text and image indexing
            for text_entry in pdf_content['text']:
                # Find any images for this page
                page_images = [
                    img_entry for img_entry in pdf_content['images']
                    if img_entry['page_num'] == text_entry['page_num']
                ]
 
                # Prepare image metadata if any images exist
                image_base64 = page_images[0]['base64_image'] if page_images else None
 
                # Index document with text and optional image
                self.index_document(
                    content=text_entry['text'],
                    title=f"{filename} - Page {text_entry['page_num']}",
                    url=blob_name,
                    filepath=filename,
                    keywords=keywords,
                    chunk_id=text_entry['page_num'],
                    has_image=document_has_images,
                    image_base64=image_base64
                )
 
            return True
        except Exception as e:
            logger.error(f"Indexing with images error: {e}")
            return False
       
    def _extract_keywords(self, text_entries):
        """
        Simple keyword extraction method.
        Replace with a more sophisticated method like TF-IDF or NLP-based keyword extraction.
        """
        # Combine all text entries
        full_text = ' '.join([entry['text'] for entry in text_entries])
       
        # Basic keyword extraction (very simple implementation)
        # You might want to use more advanced NLP techniques
        import re
        from collections import Counter
 
        # Remove stopwords and punctuation
        words = re.findall(r'\b\w+\b', full_text.lower())
        word_counts = Counter(words)
       
        # Get top 10 keywords
        keywords = [word for word, count in word_counts.most_common(10)]
        return keywords
 
    def get_chatbot_response(self, user_input):
        """Enhanced chatbot response with image context"""
        try:
            # Perform semantic search
            search_results = self.search_client.search(
                search_text=user_input,
                select=["id", "content", "title", "filepath", "has_image", "image_base64"],
                query_type="semantic",
                semantic_configuration_name="default",
                top=5
            )
           
            results = list(search_results)
           
            # Prepare context with text and image references
            context_parts = []
            context_images = []
           
            for result in results:
                context_part = f"Document Title: {result.get('title', 'Untitled')}\n"
                context_part += f"Content: {result.get('content', 'No content')[:500]}...\n---\n"
                context_parts.append(context_part)
               
                # Check for images
                if result.get('has_image') and result.get('image_base64'):
                    context_images.append({
                        'title': result.get('title', 'Untitled Image'),
                        'base64_image': result.get('image_base64')
                    })
 
            context = "\n".join(context_parts)
           
            # Prepare messages for OpenAI
            messages = [
                {
                    "role": "system",
                    "content": """AI Assistant Guidelines: Information Retrieval and Source Verification
                        Core Requirements
                        Initial Interaction

                        Begin each response with an appropriate greeting for "Hi," "Hello," etc.
                        Immediately identify available source documents in the context

                        Information Standards

                        All information must come from verified sources in the provided context
                        No information shall be provided without explicit source documentation
                        Only reference documents specifically mentioned in the context
                        Each piece of information must be thoroughly detailed and explained

                        Response Structure
                        Main Content Format

                        Begin with clear topic identification
                        Present comprehensive information using numbered points
                        Address multiple queries systematically and thoroughly
                        Include all relevant details from verified sources
                        Use clear section headers for complex topics

                        Reference Format
                        All sources must be listed at the end of the response using this structure:
                        Sources and References:

                        [Document Title]
                        URL: https://modelchatbotstorage.blob.core.windows.net/botstorage/
                        Referenced in sections: {list relevant section numbers}

                        Example:
                        [Product Storage Start to Finish PDF]
                        URL: https://modelchatbotstorage.blob.core.windows.net/botstorage/
                        Referenced in sections: 1.2, 2.1, 3.4
                        Verification Protocol
                        Source Validation

                        List all available source PDFs from the context
                        Verify information against these sources
                        Include only information with clear source attribution
                        Document exact PDF names as they appear in context

                        Error Handling
                        When source is unclear:
                        "I apologize, but while I have found information regarding [topic], I cannot verify its source document. I can only provide information with confirmed sources from the provided PDFs."
                        When information is unavailable:
                        "I apologize, but I cannot provide information about [topic] as it is not present in the provided PDF documents."
                        Quality Assurance

                        Double-verify all source references
                        Maintain consistent formatting throughout
                        Exclude any information lacking clear attribution
                        Cross-reference all citations with context
                        Use full document names without modifications

                        Example Response Structure:
                        Greeting: "Hello! I'll help you with your query."

                        Topic: [Clear statement of subject]

                        Detailed Information:
                        1. [Comprehensive point with full context]
                        2. [Detailed explanation with specific data]
                        3. [Additional relevant information]

                        Sources and References:

                        [Exact Document Name as in Context]
                        URL: {verified SharePoint URL}
                        Referenced in sections: {specific points}
                        
                        
                    """
                },
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuery: {user_input}"
                }
            ]
           
            # Generate response with more robust error handling
            try:
                response = openai.ChatCompletion.create(
                    engine=self.deployment_name,
                    messages=messages,
                    temperature=0.5,
                    max_tokens=6500
                )
               
                GREETING_PATTERNS = ["hi", "hello", "hey"]
                WELLBEING_PATTERNS = ["how are you", "how're you", "how do you do"]
                IDENTITY_PATTERNS = ["what is your name", "who are you", "what are you"]
               
                chatbot_response = { 'text': response, 'images': context_images }
                print("JSON FROMAT:",json.dumps(chatbot_response['text'], indent=2))
                input_lower = user_input.lower()
                if  "hi" in user_input.lower():
                     return {
                    'text': response.choices[0].message.content,
                   
                }
                elif any(pattern in input_lower for pattern in WELLBEING_PATTERNS):
                     return {
                    'text': response.choices[0].message.content
                    }
                elif any(pattern in input_lower for pattern in GREETING_PATTERNS):
                     return {
                    'text': response.choices[0].message.content
                    }
                elif any(pattern in input_lower for pattern in IDENTITY_PATTERNS):
                     return {
                    'text': response.choices[0].message.content
                    }
                       
                else:
                    return{
                            'text': response.choices[0].message.content,
                            'images': context_images
                        }
            except openai.error.APIError as e:
                logger.error(f"OpenAI API Error: {e}")
                return {
                    'text': f"An OpenAI service error occurred: {str(e)}. Please try again later.",
                    'images': context_images
                }
            except openai.error.AuthenticationError as e:
                logger.error(f"OpenAI Authentication Error: {e}")
                return {
                    'text': "There was an authentication issue with the AI service. Please check your credentials.",
                    'images': context_images
                }
            except openai.error.RateLimitError as e:
                logger.error(f"OpenAI Rate Limit Error: {e}")
                return {
                    'text': "The AI service is currently overloaded. Please try again in a few minutes.",
                    'images': context_images
                }
   
        except Exception as e:
            logger.error(f"Chatbot response error: {e}")
            return {
                'text': f"An unexpected error occurred: {str(e)}",
                'images': []
            }
 
    def upload_pdf(self):
        """Handle PDF upload with image extraction"""
        if flask.request.method == 'POST':
            if 'pdf_file' not in flask.request.files:
                flash('No file uploaded')
                return redirect(flask.request.url)
 
            pdf_file = flask.request.files['pdf_file']
 
            if pdf_file.filename == '':
                flash('No selected file')
                return redirect(flask.request.url)
 
            if pdf_file:
                filename = os.path.join(self.app.config['UPLOAD_FOLDER'], pdf_file.filename)
                pdf_file.save(filename)
 
                try:
                    # Extract PDF content (text and images)
                    pdf_content = self.extract_pdf_content(filename)
                   
                    # Upload to blob storage
                    blob_name = self.upload_pdf_to_blob(filename)
 
                    if blob_name:
                        # Index document with extracted content
                        self.index_document_with_images(pdf_content, pdf_file.filename, blob_name)
                        flash('PDF uploaded with text and images successfully!')
                    else:
                        flash('Error uploading PDF')
 
                except Exception as e:
                    logger.exception(f'PDF processing error: {e}')
                    flash(f'Error processing PDF: {str(e)}')
                finally:
                    # Clean up temporary file
                    try:
                        os.remove(filename)
                    except Exception as remove_error:
                        logger.error(f'Temp file removal error: {remove_error}')
 
                return redirect(url_for('index'))
 
        return render_template('upload_pdf.html')
 
    def index(self):
        """Main chat interface route with session initialization"""
        # Always initialize a fresh chat history
        session['chat_history'] = session.get('chat_history', [])
       
        if flask.request.method == 'POST':
            if flask.request.form.get('action') == 'clear':
                return self.clear()
            elif flask.request.form.get('action') == 'new_project':
                # Start a new project by clearing the chat history
                session['chat_history'] = []
                flash('New project started! Chat history cleared.')
                print("url is : ",url_for)
                return redirect(url_for('index'))

            user_input = flask.request.form.get('user_input')
            if user_input:
                response = self.get_chatbot_response(user_input)
               
                # Update chat history with text and images
                session['chat_history'].append({
                    'role': 'user',
                    'content': user_input
                })
                if "sorry" in response['text'].lower():
                    session['chat_history'].append({
                        'role': 'assistant',
                        'content': response['text'],
                    })
                else:
                    session['chat_history'].append({
                        'role': 'assistant',
                        'content': response['text'],
                        'images': response.get('images', [])
                    })
               
                session.modified = True
                return render_template('index.html', chat_history=session['chat_history'])
       
        return render_template('index.html', chat_history=session['chat_history'])
 
    def _validate_env_vars(self):
        """Validate required environment variables"""
        required_vars = [
            'AZURE_SEARCH_SERVICE_ENDPOINT',
            'AZURE_SEARCH_ADMIN_KEY',
            'AZURE_STORAGE_CONNECTION_STRING',
            'AZURE_STORAGE_CONTAINER_NAME',
            'AZURE_OPENAI_ENDPOINT',
            'AZURE_OPENAI_API_KEY',
            'OPENAI_DEPLOYMENT_NAME',
            'AZURE_SEARCH_INDEX_NAME'
        ]
 
        for var in required_vars:
            if not os.getenv(var):
                raise ValueError(f"Missing environment variable: {var}")
 
        # Configure OpenAI to use Azure
        openai.api_type = "azure"
        openai.api_base = os.getenv('AZURE_OPENAI_ENDPOINT')
        openai.api_key = os.getenv('AZURE_OPENAI_API_KEY')
        openai.api_version = "2024-06-01"  # Use the appropriate API version
        self.deployment_name = os.getenv('OPENAI_DEPLOYMENT_NAME')
 
    def _configure_azure_services(self):
        """Configure Azure Search and Blob Storage clients"""  
        # Azure Search configuration
        endpoint = os.getenv('AZURE_SEARCH_SERVICE_ENDPOINT')
        key = os.getenv('AZURE_SEARCH_ADMIN_KEY')
        index_name = os.getenv('AZURE_SEARCH_INDEX_NAME')
 
        # Create search index client
        credential = AzureKeyCredential(key)
        self.index_client = SearchIndexClient(endpoint, credential)
       
        # Create search client
        self.search_client = SearchClient(endpoint, index_name, credential)
 
        # Azure Blob Storage configuration
        connection_string = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        container_name = os.getenv('AZURE_STORAGE_CONTAINER_NAME')
       
        # Create blob service client
        self.blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        self.container_client = self.blob_service_client.get_container_client(container_name)
 
    def _ensure_index_exists(self):
            """Create Azure Cognitive Search index if it doesn't exist"""
           
            index_name = os.getenv('AZURE_SEARCH_INDEX_NAME')
           
            # Define index fields
            fields = [
                SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                SearchableField(name="content", type=SearchFieldDataType.String, searchable=True),
                SimpleField(name="title", type=SearchFieldDataType.String, searchable=True),
                SearchableField(name="keywords", type=SearchFieldDataType.String, searchable=True),
                SimpleField(name="url", type=SearchFieldDataType.String),
                SimpleField(name="filepath", type=SearchFieldDataType.String),
                SimpleField(name="chunk_id", type=SearchFieldDataType.Int32),
                SimpleField(name="has_image", type=SearchFieldDataType.Boolean),
                SimpleField(name="image_base64", type=SearchFieldDataType.String)
            ]
 
            # Create semantic settings with proper structure
            semantic_settings = {
                "configurations": [
                    {
                        "name": "default",
                        "prioritizedFields": {
                            "titleField": {"fieldName": "title"},
                            "prioritizedContentFields": [{"fieldName": "content"}],
                            "prioritizedKeywordsFields": [{"fieldName": "keywords"}]
                        }
                    }
                ]
            }
 
            # Create index
            index = SearchIndex(
                name=index_name,
                fields=fields,
                semantic_settings=semantic_settings
            )
 
            try:
                # Try to get existing index
                existing_index = self.index_client.get_index(index_name)
                logger.info(f"Found existing index: {index_name}")
            except Exception:
                # Create index if it doesn't exist
                try:
                    self.index_client.create_index(index)
                    logger.info(f"Created search index: {index_name}")
                except Exception as e:
                    logger.error(f"Error creating index: {str(e)}")
                    raise
 
    def _setup_routes(self):
        """Set up Flask routes"""
        self.app.add_url_rule('/', 'index', self.index, methods=['GET', 'POST'])
        self.app.add_url_rule('/layout', 'layout', self.upload_pdf, methods=['GET', 'POST'])
        self.app.add_url_rule('/clear', 'clear', self.clear, methods=['POST'])
 
    def index_document(self, content, title, url, filepath, keywords='', chunk_id=0, has_image=False, image_base64=None):
        """
        Index a document in Azure Search
        """
        try:
            # Generate unique ID
            doc_id = str(uuid.uuid4())
 
            document = {
                "id": doc_id,
                "content": content,
                "title": title,
                "keywords": keywords,  # Add this line
                "url": url,
                "filepath": filepath,
                "chunk_id": chunk_id,
                "has_image": has_image
            }
 
            # Add image data if available
            if image_base64:
                document["image_base64"] = image_base64
 
            # Upload document to search index
            self.search_client.upload_documents([document])
            logger.info(f"Indexed document: {title}")
            return True
 
        except Exception as e:
            logger.error(f"Document indexing error: {e}")
            return False
 
    def upload_pdf_to_blob(self, file_path):
        """
        Upload PDF to Azure Blob Storage
        """
        try:
            # Generate unique blob name
            blob_name = os.path.basename(file_path)
           
            # Get blob client
            blob_client = self.container_client.get_blob_client(blob_name)
 
            # Upload file
            with open(file_path, "rb") as data:
                blob_client.upload_blob(data)
 
            logger.info(f"Uploaded blob: {blob_name}")
            return blob_name
 
        except Exception as e:
            logger.error(f"Blob upload error: {e}")
            return None
 
    def clear(self):
        """Clear chat history"""
        session['chat_history'] = []
        return redirect(url_for('index'))
 
    def run(self, debug=False, host='0.0.0.0', port=5000):
        """
        Run the Flask application
        """
        try:
            self.app.run(debug=debug, host=host, port=port)
        except Exception as e:
            logger.error(f"Application startup error: {e}")
            raise
 
if __name__ == '__main__':
    pdf_search_app = PDFSearchApp()
    pdf_search_app.run(debug=True)  
 