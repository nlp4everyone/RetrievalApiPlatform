# 🚀 Retrieval Engine (Retrieval Protocol) for RAG Systems

A high-performance, modular Retrieval Engine designed specifically for Retrieval-Augmented Generation (RAG) systems. This project implements a robust retrieval protocol that serves as the backbone for efficient document retrieval, enabling accurate and contextually relevant responses in RAG-based applications.

Built with scalability and performance in mind, this Retrieval Engine supports various document types, embedding models, and vector databases, making it a versatile solution for building production-grade RAG applications.

<br />

# 🧠 Core Features

### 🔍 Advanced Retrieval Protocol
- High-performance document retrieval optimized for RAG systems
- Support for multiple retrieval strategies (dense, sparse, hybrid)
- Configurable chunking and embedding pipelines

### 🗃️ Vector Database Integration
- Seamless integration with leading vector databases (Qdrant)
- Efficient similarity search with configurable indexing options
- Built-in support for metadata filtering and hybrid search

### 🚀 Performance Optimizations
- Asynchronous processing pipeline for high throughput
- Caching layer for frequently accessed documents
- Resource-efficient indexing and search operations

### 🔌 Extensible Architecture
- Standardized interfaces for easy integration with existing systems
- Comprehensive API for programmatic access and customization
- Built-in monitoring and metrics collection

<br />

# 🛠️ Prerequisites

1. **Hardware Requirements**
   - CPU: x86_64 (AVX2 support recommended)
   - RAM: 8GB minimum, 16GB+ recommended
   - Storage: SSD recommended for better I/O performance
   - GPU: Optional but recommended for faster embeddings (NVIDIA GPU with CUDA support)

2. **Software Dependencies**
   - Python 3.9+
   - Docker and Docker Compose
   - (Optional) CUDA Toolkit if using GPU acceleration

# 🚀 Quick Start

1. **Clone the repository**
   ```bash
   # Clone the main repository
   git clone -b retrieval/naive-rag https://github.com/nlp4everyone/PrivateAI.git
   # Navigate to project directory
   cd PrivateAI
   ```

2. **Set up environment configuration**
   ```bash
   # Copy the sample environment file
   cp .env.sample .env
   # Edit the .env file to customize settings like ports, API keys, etc.
   # nano .env  # or use your preferred text editor
   ```

3. **Build and start the services**
   ```bash
   # Build all Docker containers (this might take a while on first run)
   bash build_docker.sh
   
   # Start all services in detached mode
   bash run_docker.sh
   ```

4. **Monitor the services**
   ```bash
   # View logs to check if all services started successfully
   bash view_log.sh
   ```

5. **Access the services** (default ports - customize in `.env`)
   - 🔌 **API Documentation**: http://localhost:8005/docs - Interactive API docs
   - 🗄️ **Vector Store**: http://localhost:6333/dashboard - Qdrant vector database UI
   - 📦 **Object Storage**: http://localhost:9001 - MinIO dashboard (default: minioadmin/minioadmin)
   - 📊 **MLflow UI**: http://localhost:5000 - Track experiments and model versions


## 📋 To-Do List
- [x] Define base components
- [x] Update Chonkie chunking strategy
- [x] Add PDF parser (UndatasIO) module (04/01)
- [x] Complete naive search function (07/01)
- [x] Tracing retrieval with MLflow (10/01)
- [ ] Add vector store file branch
- [ ] Filter with metadata filtering
- [ ] Implement other PDF parser service (LlamaParse,etc) (soon)
- [ ] Accept processing docx document (soon)
- [ ] Attach multiple document files to vector store(soon)

# 💴 Integrations:
- 📄 Framework: Langchain, vLLM, SGLang
- 🔤 Text Chunking: [Chonkie](https://docs.chonkie.ai)
- 🗄️ User Management: Postgres
- 🔔 Task Queue: Redis + TaskIQ
- 📦 Tracking: MLflow + MinIO
- ⚙️ API Layer: FastAPI
- 🧰 Runtime: Docker Compose

<br />


