#!/usr/bin/env python
# encoding: utf-8

import json
import os
import collections
import datetime
import mmap
import re
import requests
import shlex
import sys
import time

try:
    import requests
except ImportError as e:
    print("Error! requests module could not be imported. Perhaps install it with\n\n    pip install requests")
    exit()

try:
    import requests_toolbelt
except ImportError as e:
    print("Error! requests_toolbelt module could not be imported. Perhaps install it with\n\n    pip install requests-toolbelt")
    exit()

from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

from talus_client.models import *
import talus_client.models

class TalusClient(object):

    """An api client that will communicate with Talus"""

    def __init__(self, api_base="http://localhost:8001", user=None):
        """TODO: to be defined1. """
        object.__init__(self)

        self._api_base = api_base
        self._user = user
        # TODO this was annoying, revisit this
        talus_client.models.API_BASE = api_base
    
    def model_iter(self, cls, **search):
        for item in cls.objects(api_base=self._api_base, **search):
            yield item
    
    # -------------------------
    # fileset handling
    # -------------------------
    
    def fileset_iter(self, **search):
        """Return an iterator that iterates over all existing OS models in Talus
        :returns: iterator
        """
        for fileset_ in FileSet.objects(api_base=self._api_base, **search):
            yield fileset_
    
    def fileset_find(self, name_or_id, **search):
        return self._name_or_id(FileSet, name_or_id, **search)
    
    def fileset_create(self, name, files):
        """Create a new fileset named ``name`` containing files ``files``
        """
        fileset = FileSet(api_base=self._api_base)
        self._prep_model(fileset)
        fileset.name = name
        fileset.files = files
        fileset.job = None
        now = time.time()
        fileset.timestamps = {"created": now, "modified": now}
        fileset.save()

        return fileset
    
    def fileset_delete(self, fileset_id, all_files=False):
        """Delete an os by ``os_id`` which may be the id or name
        
        :os_id: The name or id of the os to delete
        """
        fileset = self._name_or_id(FileSet, fileset_id)
        if fileset is None:
            raise errors.TalusApiError("Could not locate FileSet with name/id {!r}".format(fileset_id))

        if all_files:
            for file_id in fileset.files:
                self.corpus_delete(file_id)

        fileset.delete()

    # -------------------------
    # corpus handling
    # -------------------------

    def corpus_list(self, **filters):
        """List all files in the corpus, using the ``filters`` to search/filter
        the results
        """
        try:
            res = requests.get(self._api_base + "/api/corpus/", params=filters)
        except requests.ConnectionError as e:
            raise errors.TalusApiError("Could not connect to {}".format(self._api_base + "/api/corpus/"))

        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not list corpus files", error=res.text)

        return res.json()

    def corpus_upload(self, file_path, **extra_attrs):
        """Upload the file found at ``file_path`` into the talus corpus, adding extra_attrs
        to the file as well (TODO).

        :param str file_path:
        :returns: The id of the uploaded file
        """
        if not os.path.exists(file_path):
            raise errors.TalusApiError("Could not locate new corpus file {!r} on disk".format(file_path))

        file_id = self._upload_file(file_path, api_endpoint="corpus", **extra_attrs)
        return file_id
    
    def corpus_get(self, file_id):
        """Fetch the file with id ``file_id`` from the corpus
        """
        try:
            res = requests.get(self._api_base + "/api/corpus/{}".format(file_id))
        except requests.ConnectionError as e:
            raise errors.TalusApiError("Could not connect to {}".format(self._api_base + "/api/corpus/{}".format(file_id)))

        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not fetch corpus file with id {}".format(file_id), error=res.text)

        filename = res.headers["content-disposition"].split("attachment;")[1].strip().split("filename=")[1].strip()
        return filename,res.text
    
    def corpus_delete(self, file_id):
        """Delete the file with id ``file_id`` from the corpus
        """
        try:
            res = requests.delete(self._api_base + "/api/corpus/{}".format(file_id))
        except requests.ConnectionError as e:
            raise errors.TalusApiError("Could not connect to {}".format(self._api_base + "/api/corpus/{}".format(file_id)))

        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not delete corpus file with id {}".format(file_id), error=res.text)

        return res.json()

    # -------------------------
    # VM image handling
    # -------------------------

    def image_iter(self, **search):
        """Return an iterator that iterates over all existing images in Talus
        :returns: iterator over all existing images
        """
        for image in Image.objects(api_base=self._api_base, **search):
            yield image

    def image_import(self, image_path, image_name, os_id, desc="desc", tags=None, username="user", password="password", file_id=None):
        """TODO: Docstring for import_image.

        :image_path: The path to the image to be uploaded
        :image_name: The name of the resulting image
        :os_id: The id or name of the operating system document (string)
        :desc: A description of the image
        :tags: An array of tags associated with this VM image (e.g. ["browser", "ie", "ie10", "windows"])
        :username: The username to be used in the image
        :password: The password associated with the username
        :file_id: The id of the file that has already been uploaded to the server
        :returns: The configured image
        """
        os = self._name_or_id(OS, os_id)
        if os is None:
            raise errors.TalusApiError("Could not locate OS by id/name {!r}".format(os_id))

        uploaded_file = file_id
        if uploaded_file is None:
            print("uploading file {!r}".format(image_path))
            image_path = self._clean_path(image_path)
            uploaded_file = self._upload_file(image_path)

            print("uploaded file id: {}".format(uploaded_file))

        if tags is None:
            tags = []

        image = Image(api_base=self._api_base)
        self._prep_model(image)
        image.name = image_name
        image.os = os.id
        image.desc = desc
        image.tags = tags
        image.status = {"name": "import", "tmpfile": uploaded_file}
        image.username = username
        image.password = password
        image.timestamps = {"created": time.time()}
        image.md5 = "blahblah"

        image.save()

        return image
    
    def image_configure(self, image_id_or_name, vagrantfile=None, user_interaction=False, kvm=False):
        """Configure the image with id ``image_id``. An instance of the image will
        be spun up which you can then configure. Shutting down the image will commit
        any changes.

        :image_id_or_name: The id or name of the image that is to be configured
        :vagrantfile: The contents of a vagrantfile that is to be used to configure the image
        :user_interaction: If the user should be given a chance to manually interact
        :returns: The configured image
        """
        image = self._name_or_id(Image, image_id_or_name)
        if image is None:
            raise errors.TalusApiError("image with id or name {!r} not found".format(image_id_or_name))
            return

        #if image.status["name"] != "ready":
            #raise errors.TalusApiError("Image is not in ready state, cannot configure (state is {})".format(
                #image.status["name"]
            #))

        image.status = {
            "name": "configure",
            "kvm": kvm,
            "vagrantfile": vagrantfile,
            "user_interaction": user_interaction
        }
        image.save()

        return image
    
    def image_create(self, image_name, base_image_id_or_name, os_id, desc="", tags=None, vagrantfile=None, user_interaction=False):
        """Create a new VM image based on an existing image.

        :image_name: The name of the new VM image (to be created)
        :base_image_id_or_name: The id or name of the base image
        :os_id: The id of the operating system
        :desc: A description of the new image
        :tags: A list of tags associated with the new image
        :vagrantfile: The Vagrantfile to run when creating the new image
        :user_interaction: Allow user interaction to occur (vs automatically shutting down the VM after the vagrantfile is run)
        :returns: The created image
        """
        base_image = self._name_or_id(Image, base_image_id_or_name)
        if base_image is None:
            print("Base image with id or name {!r} not found".format(base_image_id_or_name))
            return

        base_image_id = base_image.id

        # essentially use the base_image as the base for the new image
        base_image.clear_id()
        image = base_image 
        self._prep_model(image)
        # required
        image.name = image_name
        image.base_image = base_image_id

        if os_id is not None:
            os = self.os_find(os_id)
            if os is None:
                raise errors.TalusApiError("No os found by name/id '{}'".format(os_id))
            image.os = os.id
        if desc is not None:
            image.desc = desc
        if tags is not None:
            image.tags = tags

        image.status = {
            "name": "create",
            "vagrantfile": vagrantfile,
            "user_interaction": user_interaction
        }
        image.save()

        return image
    
    def image_delete(self, image_id_or_name):
        """Delete the image with id ``image_id`` or name ``name``

        :image_id: The id of the image to delete
        :returns: None
        """
        image = Image.find_one(api_base=self._api_base, id=image_id_or_name)
        if image is None:
            image = Image.find_one(api_base=self._api_base, name=image_id_or_name)
            if image is None:
                print("image with id or name {!r} not found".format(image_id_or_name))
                return

        image.status = {
            "name": "delete"
        }
        image.save()
        return image

    # -------------------------
    # VM os handling
    # -------------------------

    def os_iter(self, **search):
        """Return an iterator that iterates over all existing OS models in Talus
        :returns: iterator
        """
        for os_ in OS.objects(api_base=self._api_base, **search):
            yield os_
    
    def os_find(self, name_or_id, **search):
        return self._name_or_id(OS, name_or_id, **search)
    
    def os_delete(self, os_id):
        """Delete an os by ``os_id`` which may be the id or name
        
        :os_id: The name or id of the os to delete
        """
        os_ = self._name_or_id(OS, os_id)
        if os_ is None:
            raise errors.TalusApiError("Could not locate os with name/id {!r}".format(os_id))
        if len(Image.objects(api_base=self._api_base, os=os_.id)) > 0:
            raise errors.TalusApiError("Could not delete OS, more than one image references it")
        os_.delete()

    # -------------------------
    # code handling
    # -------------------------

    def code_iter(self, type_=None, **search):
        """Return an iterator that iterates over all existing Code models in Talus
        :returns: iterator
        """
        filter_ = search
        if type_ is not None:
            filter_["type"] = type_
        for code in Code.objects(api_base=self._api_base, **filter_):
            yield code
    
    def code_find(self, name_or_id, **search):
        return self._name_or_id(Code, name_or_id, **search)
    
    def code_create(self, code_name, code_type, tags=None):
        """Create the code, and return the results"""
        data = {
            "name": code_name,
            "type": code_type,
        }

        if self._user is not None:
            if tags is None:
                tags = []
            if self._user not in tags:
                tags.append(self._user)

        if tags is not None:
            data["tags"] = json.dumps(tags)

        e = MultipartEncoder(fields=data)

        try:
            res = requests.post(self._api_base + "/api/code/create/",
                data    = e,
                headers    = {"Content-Type": e.content_type}
            )
        except requests.ConnectionError as e:
            raise errors.TalusApiError("Could not connect to {}".format(self._api_base + "/api/code/create"))
        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not create code!", error=res.text)

        return json.loads(res.text)

    # -------------------------
    # task handling
    # -------------------------

    def task_find(self, name_or_id, **search):
        """Find the task
        """
        return self._name_or_id(Task, name_or_id, **search)

    def task_iter(self, **search):
        """Return an iterator that iterates over all existing Task models in Talus
        :returns: iterator
        """
        for task in Task.objects(api_base=self._api_base, **search):
            yield task

    def task_create(self, name, tool_id, params, version=None, limit=1, vm_max="30m"):
        """Create a new task with the supplied arguments

        :name: The name of the task
        :tool_id: The id or name of the tool the task will run
        :params: A dict of params for the task
        :version: The version of code to use. None defaults to the HEAD version (default: None)
        :limit: The default limit of any jobs that use this task
        :returns: The task model
        """
        tool = self._name_or_id(Code, tool_id, type="tool")
        if tool is None:
            raise errors.TalusApiError("Could not locate Tool by id/name {!r}".format(tool_id))
        if not isinstance(params, dict):
            raise errors.TalusApiError("params must be a dict!")

        task = Task(api_base=self._api_base)
        self._prep_model(task)
        task.name = name
        task.tool = tool.id
        task.version = version
        task.params = params
        task.limit = limit
        task.vm_max = self._total_seconds_from_string(vm_max)
        task.save()
    
    def task_delete(self, task_id):
        """Delete a task by ``task_id`` which may be the id or name
        
        :task_id: The name or id of the task to delete
        """
        task = self._name_or_id(Task, task_id)
        if task is None:
            raise errors.TalusApiError("Could not locate task with name/id {!r}".format(task_id))
        task.delete()
        
    # -------------------------
    # result handling
    # -------------------------

    def result_iter(self, **search):
        """Iterate through result matching the search criteria

        :search: optional search parameters
        """
        for result in Result.objects(api_base=self._api_base, **search):
            yield result
        
    # -------------------------
    # slave handling
    # -------------------------

    def slave_iter(self, **search):
        """Iterate through all of the slaves

        :search: optional search parameters
        """
        for slave in Slave.objects(api_base=self._api_base, **search):
            yield slave
        
    # -------------------------
    # master handling
    # -------------------------

    def master_get(self):
        res = Master.objects(api_base=self._api_base)
        if len(res) == 0:
            raise errors.TalusApiError("No master model has been created in the DB! Is it not running?")
        return res[0]
        
    # -------------------------
    # job handling
    # -------------------------

    def job_find(self, name_or_id, **search):
        return self._name_or_id(Job, name_or_id, **search)

    def job_iter(self, **search):
        """Iterate through all of the jobs

        :search: optional search parameters
        """
        for job in Job.objects(api_base=self._api_base, **search):
            yield job
    
    def job_create(self, task_name_or_id, image=None, name=None, params=None, priority=50, queue="jobs", limit=1, vm_max=None, network="whitelist", debug=False, tags=None):
        """Create a new job (run a task)"""
        task = self._name_or_id(Task, task_name_or_id)
        if task is None:
            raise errors.TalusApiError("could not locate task with id/name {!r}".format(task_name_or_id))

        if task.image is None and image is None:
            raise errors.TalusApiError("No image was defined in the task, and no image was specified. Give me mah image!")

        if task.image is not None and image is None:
            image_obj = self._name_or_id(Image, task.image)
        else:
            image_obj = self._name_or_id(Image, image)

        if image_obj is None:
            raise errors.TalusApiError("could not locate image with id/name {!r}".format(image))
        image = image_obj

        if image.status["name"] != "ready":
            raise errors.TalusApiError("image '{}' ({}) is not in ready state (state is {})".format(
                image.name,
                image.id,
                image.status["name"]
            ))

        if name is None:
            name = task.name + " " + str(datetime.datetime.now())

        if limit is None:
            limit = task.limit

        # any params set will UPDATE the default params, not override them
        base_params = task.params
        if params is not None:
            base_params = self._dict_nested_updated(base_params, params)

        # inherit from the task if not specified
        if vm_max is None:
            vm_max = task.vm_max
        else:
            vm_max = self._total_seconds_from_string(vm_max)

        job = Job(api_base=self._api_base)
        self._prep_model(job)
        job.name = name
        job.image = image.id
        job.params = base_params
        job.task = task.id
        job.status = {"name": "run"}
        job.timestamps = {"created": time.time()}
        job.priority = priority
        job.queue = queue
        job.limit = limit
        job.vm_max = vm_max
        job.network = network
        job.debug = debug

        if tags is not None and isinstance(tags, list):
            job.tags += tags

        job.save()
        
        return job
    
    def job_cancel(self, job_name_or_id, job=None):
        """Cancel the job ``job_name_or_id`` in talus

        :job_name_or_id: The job name or id to cancel
        """
        if job is None:
            job = self._name_or_id(Job, job_name_or_id)

        if job is None:
            raise errors.TalusApiError("could not locate job with name or id {!r}".format(job_name_or_id))

        job.status = {"name": "cancel"}
        job.save()

        return job
        
    # -------------------------
    # utility
    # -------------------------

    def _prep_model(self, model):
        if hasattr(model, "tags") and self._user is not None and self._user not in model.tags:
            model.tags.append(self._user)

    def _total_seconds_from_string(self, val):
        match = re.match(r'(\d+h)?(\d+m)?(\d+s)?', val)

        unit_mult = {
            "h":60*60,
            "m":60,
            "s":1
        }

        total_seconds = 0
        if match is not None:
            for item in match.groups():
                if item is None:
                    continue
                val = int(item[:-1])
                unit = item[-1]
                total_seconds += val * unit_mult[unit]
        else:
            total_seconds = int(val)

        return total_seconds
    
    def _verify(self, prompt):
        while True:
            answer = raw_input(prompt)
            if answer.lower()[0] not in ["y", "n"]:
                print("incorrect answer, y/n only. please.")
                continue
            break

        return answer == "y"

    def _dict_nested_updated(self, base, new):
        """Update a nested dictionary

        :base: the base dict
        :new: the new values for the dict
        :returns: the updated dict
        """ 
        for k,v in new.iteritems():
            if isinstance(v, collections.Mapping):
                r = self._dict_nested_updated(base.get(k, {}), v)
                base[k] = r
            else:
                base[k] = new[k]
        return base
    
    def _name_or_id(self, cls, name_or_id, **extra):
        """Find model by name or id

        :name_or_id: The name or id of the model
        :extra: Any additional search/filter arguments
        :returns: The first model if found, else None
        """
        res = cls.find_one(api_base=self._api_base, id=name_or_id, **extra)
        if res is None:
            res = cls.find_one(api_base=self._api_base, name=name_or_id, **extra)
            if res is None:
                return None
        return res

    def _upload_file(self, path, api_endpoint="upload", **extra_params):
        """Upload the file found at ``path`` to talus, returning an id

        :path: The (local) path to the file
        :returns: An id for the remote file

        """
        if not os.path.exists(path):
            raise errors.TalusApiError("Cannot upload image, path {!r} does not exist".format(path))

        total_size = os.path.getsize(path)
        self.last_update = ""
        def print_progress(monitor):
            sys.stdout.write("\b" * len(self.last_update))
            percent = float(monitor.bytes_read) / monitor.len

            update = "{:0.2f}%".format(percent * 100)
            if len(update) < 7:
                u = " " * (7 - len(update)) + update

            if len(update) < len(self.last_update):
                update += " " * (len(self.last_update) - len(update))
            sys.stdout.write(update)
            sys.stdout.flush()
            self.last_update = update
        
        data = {
            "file": (os.path.basename(path), open(path, "rb"), "application/octet-stream")
        }
        data.update(extra_params)

        e = MultipartEncoder(fields=data)
        m = MultipartEncoderMonitor(e, print_progress)

        try:
            res = requests.post(
                self._api_base + "/api/{}/".format(api_endpoint),
                data=m,
                headers={"Content-Type":e.content_type},
                timeout=(60*60) # super long timeout for uploading massive files!
            )
        except requests.ConnectionError as e:
            raise errors.TalusApiError("Could not connect to {}".format(self._api_base + "/api/{}/".format(api_endpoint)))

        # clear out the last of the progress percent that was printed
        print("\b" * len(self.last_update))

        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not upload file!", error=res.text)

        if res.text[0] in ["'", '"']:
            return res.text[1:-1]

        return res.text

    def _api(self, path):
        """Join the api base with path"""
        return self._api_base + "/" + path
    
    def _clean_path(self, path):
        return os.path.realpath(os.path.expanduser(path))
