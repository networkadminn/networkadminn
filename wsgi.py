"""WSGI entry for aaPanel / gunicorn / uWSGI.

aaPanel fields:
  Entry file:       wsgi.py
  Application Name: app
  Comm Protocol:    WSGI
  Project Port:     8080  (or any free port; reverse-proxy to it)
"""

from timetrack.server.app import create_app

app = create_app()
