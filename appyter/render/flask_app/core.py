import os
import uuid
import json
from flask import Blueprint, request, redirect, abort, send_file, send_from_directory, url_for, current_app, jsonify

from appyter.context import get_jinja2_env
from appyter.ext.fs import Filesystem
from appyter.parse.nb import nb_from_ipynb_io, nb_to_ipynb_io
from appyter.util import secure_filename
from appyter.render.form import render_form_from_nbtemplate
from appyter.render.nbviewer import render_nbviewer_from_nb
from appyter.render.nbconstruct import render_nb_from_nbtemplate
from appyter.render.nbinspect import render_nbtemplate_json_from_nbtemplate
from appyter.render.flask_app.download import upload_from_request
from appyter.render.flask_app.util import sanitize_sha1sum, sanitize_uuid, route_join_with_or_without_slash, collapse, sha1sum_io, sha1sum_dict


core = Blueprint('__main__', __name__)

_fields = None
def get_fields():
  ''' Helper to get/cache fields even if we're on a different thread
  '''
  global _fields
  if not _fields:
    fs = Filesystem(current_app.config['CWD'])
    with fs.open(current_app.config['IPYNB'], 'r') as fr:
      env = get_jinja2_env(config=current_app.config)
      nbtemplate = nb_from_ipynb_io(fr)
      _fields = render_nbtemplate_json_from_nbtemplate(env, nbtemplate)
  return _fields

_ipynb_hash = None
def get_ipynb_hash():
  global _ipynb_hash
  if not _ipynb_hash:
    fs = Filesystem(current_app.config['CWD'])
    _ipynb_hash = sha1sum_io(fs.open(current_app.config['IPYNB'], 'rb'))
  return _ipynb_hash

def prepare_data(data):
  # TODO: assert constraints
  return data

def prepare_formdata(req):
  ''' Get data defined for each field from json/form. Also process POSTed files
  '''
  fields = get_fields()
  file_fields = {
    field['args']['name']
    for field in fields
    if field['field'] == 'FileField'
  }
  data = {}
  if req.form:
    data.update({
      field['args']['name']: collapse(V)
      for field in fields
      for V in req.form.getlist(field['args']['name'])
      if field['args']['name'] in req.form
    })
    data.update(upload_from_request(req, file_fields))
  elif req.json:
    data.update({
      field['args']['name']: v
      for field in fields
      for v in req.json.get(field['args']['name'])
      if field['args']['name'] in req.json
    })
  return prepare_data(data)

def prepare_results(data):
  results_hash = sha1sum_dict(dict(ipynb=get_ipynb_hash(), data=data))
  data_fs = Filesystem(current_app.config['DATA_DIR'])
  results_path = Filesystem.join('output', results_hash)
  if not data_fs.exists(Filesystem.join(results_path, current_app.config['IPYNB'])):
    # construct/write files into directory
    fields = get_fields()
    file_fields = {
      field['args']['name']
      for field in fields
      if field['field'] == 'FileField'
    }
    # link all input files into output directory
    for file_field in file_fields:
      if fdata := data.get(file_field):
        content_hash, filename = fdata.split('/', maxsplit=1)
        content_hash = sanitize_sha1sum(content_hash)
        filename = secure_filename(filename)
        data_fs.link(
          Filesystem.join('input', content_hash),
          Filesystem.join(results_path, filename)
        )
        data[file_field] = filename
    # construct/write notebook
    env = get_jinja2_env(config=current_app.config, context=data)
    fs = Filesystem(current_app.config['CWD'])
    with fs.open(current_app.config['IPYNB'], 'r') as fr:
      nbtemplate = nb_from_ipynb_io(fr)
    nb = render_nb_from_nbtemplate(env, nbtemplate)
    nbfile = Filesystem.join(results_path, os.path.basename(current_app.config['IPYNB']))
    with data_fs.open(nbfile, 'w') as fw:
      nb_to_ipynb_io(nb, fw)
  #
  return results_hash

@route_join_with_or_without_slash(core, methods=['POST'])
def post_index():
  data = prepare_formdata(request)
  result_hash = prepare_results(data)
  mimetype = request.accept_mimetypes.best_match([
    'text/html',
    'application/json',
  ], 'text/html')
  if mimetype in {'text/html'}:
    return redirect(url_for('__main__.data_files', path=result_hash + '/'), 303)
  elif mimetype in {'application/json'}:
    return jsonify(dict(session_id=result_hash))
  else:
    abort(404)
