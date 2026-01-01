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

1. Clone the repository:
```bash
git clone https://github.com/nlp4everyone/RetrievalEngine.git
cd RetrievalEngine
```

2. Set up environment:
```bash
cp .env.sample .env
# Edit .env file with your configuration
```

3. Build and start services:
```bash
# Build the Docker containers
bash build_docker.sh

# Start all services
bash run_docker.sh
```

4. Verify the installation:
```bash
# Check service status
bash view_log.sh
```


## 📋 To-Do List
- [x] Define base components
- [x] Update Chonkie chunking strategy
- [ ] Add docx, pdf parser module
- [ ] Complete search function
- [ ] Tracing retrieval with MLflow
- [ ] Filter with metadata filtering


# 💴 Integrations:
- 📄 Framework: Langchain, vLLM, SGLang
- 🔤 Text Chunking: [Chonkie](https://docs.chonkie.ai)
- 🗄️ User Management: Postgres
- 🔔 Task Queue: Redis + TaskIQ
- 📦 Tracking: MLflow + MinIO
- ⚙️ API Layer: FastAPI
- 🧰 Runtime: Docker Compose

<br />


