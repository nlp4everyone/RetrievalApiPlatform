from openai import OpenAI

# Initialize OpenAI client with local server endpoint
client = OpenAI(
    base_url="http://localhost:8005/v1",
    api_key="token"
)

# Path to the Vietnamese Wikipedia PDF file
file_path = "resources/Nguyễn An – Wikipedia tiếng Việt.pdf"

# Upload the PDF file to OpenAI's file storage
# The file will be available for fine-tuning and vector store operations
# expires_after: File will be automatically deleted after 30 days (2592000 seconds)
files = client.files.create(
    file=open(file_path, "rb"),
    purpose="fine-tune",
    expires_after={
        "anchor": "created_at",
        "seconds": 2592000
    }
)

# Display information about the uploaded file
print("=== File Uploaded Successfully ===")
print(f"File ID: {files.id}")
print(f"Created At: {files.created_at}")

# Create a vector store containing the uploaded file
# This enables semantic search and RAG (Retrieval Augmented Generation) capabilities
vector_store = client.vector_stores.create(
    name="Support FAQ",
    file_ids=[files.id]
)

# Display detailed information about the created vector store
print("=== Vector Store Created Successfully ===")
print(f"Vector Store ID: {vector_store.id}")
print(f"Created At: {vector_store.created_at}")
