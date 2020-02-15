ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8

RUN apk add --no-cache \
    	jq \
        py-pip \
	python \
	python-dev \
	python3 \
	python3-dev \
 && pip install -U pip \
 && pip3 install -U pip \
 && pip install -U virtualenv

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m virtualenv --python=/usr/bin/python3 $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py crestron.py utils.py ./

CMD ["python", "main.py"]