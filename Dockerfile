FROM python:3.10-slim

# OpenCV အတွက် System Library ထည့်ပေးရန်
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ခင်ဗျားရဲ့ bot ဖိုင်၊ ပြီးတော့ JSON ဖိုင်တွေကိုပါ ကူးထည့်
COPY bot2.py .
COPY auth_list.json .
COPY result.json .

CMD ["python", "bot2.py"]