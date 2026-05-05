#!/bin/bash
python -c "
import streamlit, os, re
path = os.path.join(os.path.dirname(streamlit.__file__), 'static/index.html')
html = open(path).read()
html = re.sub(r'<html[^>]*>', '<html lang=\"ko\" translate=\"no\">', html, count=1)
open(path, 'w').write(html)
"
streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
