FROM python:3.11-slim

# Set workdir
WORKDIR /workspace

# System deps
RUN apt-get update && apt-get install -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements core files and install
COPY requirements/requirements_core.txt .
# Install requirements
RUN pip install -r requirements_core.txt

# Copy requirements db files and install
COPY requirements/requirements_db.txt .
# Install requirements
RUN pip install -r requirements_db.txt

# Copy requirements component files and install
COPY requirements/requirements_component.txt .
# Install requirements
RUN pip install -r requirements_component.txt

# Copy the rest of the application
COPY . /workspace

EXPOSE 8005
