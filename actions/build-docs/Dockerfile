FROM asciidoctor/docker-asciidoctor:1.100.0@sha256:93ea06944afbc8e7a74ea31348b5f0624f1dfc69fa038ead3e7c4838faaa27d7

RUN apk add --no-cache chromium nodejs npm python3 \
    && npm install --global @mermaid-js/mermaid-cli@11.16.0 \
    && npm cache clean --force

ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium-browser

COPY build_docs.py /opt/roc-docs/build_docs.py
COPY rouge_roc.rb /opt/roc-docs/rouge_roc.rb
COPY mermaid-puppeteer.json /opt/roc-docs/mermaid-puppeteer.json
COPY theme /opt/roc-docs/theme
