FROM python:3

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt --no-cache-dir

COPY mail-mirror.py .
COPY bot_config.py.template bot_config.py

CMD [ "./mail-mirror.py" ]
