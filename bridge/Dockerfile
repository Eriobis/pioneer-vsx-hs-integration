FROM python:3.13-alpine
RUN pip install --no-cache-dir pyserial==3.5
COPY bridge.py /bridge.py
CMD ["python", "-u", "/bridge.py"]
