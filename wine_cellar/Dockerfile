ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /opt/wine
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
