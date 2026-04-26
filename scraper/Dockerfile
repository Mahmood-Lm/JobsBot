FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy
ARG FUNCTION_DIR="/var/task"
RUN mkdir -p ${FUNCTION_DIR}
WORKDIR ${FUNCTION_DIR}
COPY . ${FUNCTION_DIR}
RUN pip install --no-cache-dir -r requirements.txt
ENTRYPOINT [ "/usr/bin/python", "-m", "awslambdaric" ]
CMD [ "main.lambda_handler" ]