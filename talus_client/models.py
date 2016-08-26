#!/usr/bin/env python
# encoding: utf-8

from bson import json_util
import copy
import json
import os
import re
import sys


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

from requests_toolbelt.multipart.encoder import MultipartEncoder

import talus_client.errors as errors
import talus_client.utils as utils

# for the lazy
API_BASE = "http://localhost:8000"
def set_base(new_base):
    global API_BASE
    API_BASE = new_base

class Field(object):
    def __init__(self, default_value=None, details=False, desc="", validation=None):
        self.value = default_value
        self.details = details
        self.desc = desc
        self.validation = validation
    
    def get_val(self):
        return self.value
    
    def dup(self):
        return self.__class__(self.get_val())
    
    def validate(self, v):
        if self.validation is None:
            return True

        return self.validation(v)
    
    def cast(self, v):
        if self.value is None:
            return v

        if type(self.value) is bool:
            return v.lower() in ["true", "t", "yes", "y"]

        # e.g. int(10), str("yo"), etc.
        return type(self.value)(v)

    def __getitem__(self, name):
        if hasattr(self.value, "__getitem__"):
            return self.value.__getitem__(name)
        else:
            raise AttributeError
    
    def __setitem__(self, name, value):
        if hasattr(self.value, "__setitem__"):
            return self.value.__setitem__(name, value)
        else:
            raise AttributeError

class RefField(Field):
    def __init__(self, cls_name, default_value=None, details=False, search=None, desc=""):
        Field.__init__(self, default_value, details, desc=desc)
        self.cls_name = cls_name
        self.search = search
    
    def get_ref_cls(self):
        return getattr(sys.modules[__name__], self.cls_name)
    
    def get_val(self):
        if isinstance(self.value, dict) and "id" in self.value:
            return self.value["id"]
        if isinstance(self.value, dict) and "_id" in self.value:
            # mean's it doesn't exist anymore b/c djanog-rest-framework-mongoengine (ugh)
            # couldn't dereference the ReferenceField
            return "!" + self.value["_id"]["$oid"]
        if isinstance(self.value, dict) and "$id" in self.value:
            # mean's it doesn't exist anymore b/c djanog-rest-framework-mongoengine (ugh)
            # couldn't dereference the ReferenceField
            return "!" + self.value["$id"]["$oid"]
        return self.value

class TalusModel(object):
    """The baseclass for Talus API models"""

    # the path of the model, e.g. "api/os"
    api_path = ""
    interactive_create_command = None
    
    # the defined fields, with default values
    fields = {}

    @classmethod
    def api_url(cls, base):
        """Add the base path and the model's api_path together"""
        if base is None:
            base = API_BASE

        return "{}/{}/".format(
            base,
            cls.api_path
        )
    
    @classmethod
    def headers(cls):
        res = ["id"]

        if "name" in cls.fields:
            res.append("name")

        if "hostname" in cls.fields:
            res.append("hostname")

        for k,v in cls.fields.iteritems():
            if k in res or v.details:
                continue
            res.append(k)
        return res
    
    @classmethod
    def find_one(cls, api_base=None, **search):
        """Return the first matching model, or None if none matched

        :api_base: The base of the api url, If None, models.API_BASE will be used
        :**search: The search params
        :returns: The matched model or None
        """
        res = cls.objects_raw(api_base, **search)
        if len(res) == 0:
            return None
        model = cls(**res[0])
        model.api_base = api_base
        return model

    @classmethod
    def objects(cls, api_base=None, **search):
        """Return a list of models

        :api_base: The base of the api url. If none, models.API_BASE will be used
        :**search: search params
        :returns: A list of models

        """
        res = []
        for item in cls.objects_raw(api_base, **search):
            model = cls(**item)
            model.api_base = api_base
            res.append(model)
        return res

    @classmethod
    def objects_raw(cls, api_base=None, **search):
        """Return a list of json objects

        :api_base: The base of the api url. If none, models.API_BASE will be used
        :**search: search params
        :returns: A list of models as json objects (raw)

        """
        r = utils.json_request(requests.get, cls.api_url(api_base), params=search)
        try:
            res = r.json()
            return res
        # TODO maybe there should be better error handling here??
        except:
            return []

    def __init__(self, api_base=None, **fields):
        """Create a new model from a dictionary of its fields
        
        :**fields: dictionary of the model's fields"""
        if len(fields) == 0:
            fields = {}
            for k,v in self.fields.iteritems():
                fields[k] = v.dup()

        self._populate(fields)
        object.__setattr__(self, "api_base", api_base)
    
    # --------------------
    # other
    # --------------------

    def is_existing(self):
        return "id" in self._fields

    def clear_id(self):
        if "id" in self._fields:
            del self._fields["id"]

    def save(self):
        """Save this model's fields
        """
        files = None
        data = json.dumps(self._filtered_fields())

        if "id" in self._fields:
            res = utils.json_request(
                requests.put,
                self._id_url(),
                data=data
            )
        else:
            res = utils.json_request(
                requests.post,
                self.api_url(self.api_base),
                data=data
            )

        # yes, that's intentional (the //) - look it up
        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not save model", error=res.text)

        self._populate(res.json())
    
    def delete(self):
        """Delete this model
        """
        res = utils.json_request(requests.delete, self._id_url())
        if res.status_code // 100 != 2:
            raise errors.TalusApiError("Could not delete model", error=res.text)
        self._fields = {}
    
    def refresh(self):
        """Refresh the current model
        """
        if "id" not in self._fields:
            return
        matches = self.objects_raw(api_base=self.api_base, id=self.id)
        if len(matches) == 0:
            raise errors.TalusApiError("Error! current model no longer exists!")
        update = matches[0]
        self._populate(update)
    
    def _populate(self, fields):
        """Populate this model's values from the given fields

        :fields: a dict of field values
        """
        res = {}
        for k,v in self.__class__.fields.iteritems():
            res[k] = v.dup()
            if k in fields:
                if isinstance(fields[k], Field):
                    res[k].value = fields[k].get_val()
                else:
                    res[k].value = fields[k]

        for k,v in fields.iteritems():
            if k not in res:
                if isinstance(v, Field):
                    res[k] = v.get_val()
                else:
                    res[k] = v

        object.__setattr__(self, "_fields", res)
    
    def _filtered_fields(self):
        res = {}
        for k,v in self._fields.iteritems():
            if isinstance(v, Field):
                v = v.get_val()
            if v is None:
                continue
            res[k] = v
        return res
    
    def _id_url(self):
        return self.api_url(self.api_base) + self.id + "/"
    
    def __iter__(self):
        """Used for printing the model in a table"""
        for name in self.headers():
            v = self._fields[name]
            if isinstance(v, Field):
                v = v.get_val()
            yield str(v)[0:40]
    
    def __getattr__(self, name):
        if name in self._fields:
            if isinstance(self._fields[name], Field):
                return self._fields[name].get_val()
            else:
                return self._fields[name]
        raise KeyError(name)
    
    def __setattr__(self, name, value):
        if name not in self._fields:
            return object.__setattr__(self, name, value)

        if isinstance(value, TalusModel):
            value = value.id

        if isinstance(self._fields[name], Field):
            self._fields[name].value = value
        else:
            self._fields[name] = value

class Task(TalusModel):
    """The model for Tasks"""
    api_path = "api/task"
    interactive_create_command = "task create --shell"
    fields = {
        "name": Field("", desc="The name of the task"),
        "tool": RefField("Code", search={"type": "tool"}, desc="The tool the task should run"),
        "image": RefField("Image", desc="The default image for this task"),
        "params": Field({}, details=True, desc="The parameters for the tool"),
        "version": Field("", desc="The version of code the task should run on (leave blank for default)"),
        "timestamps": Field({}, details=True, desc="Timestamps (don't mess with this)"),
        "limit": Field(1, desc="Limit for the task (when does it finish?)"),
        "vm_max": Field(60*30, desc="Max vm duration (s) before being forcefully terminated"), # seconds
        "network": Field("whitelist", desc="The network whitelist (E.g. 'whitelist:DOMAIN_1,IP_2,DOMAIN_3,...')"),
        "tags" : Field([], desc="Tags associated with this task")
    }

class Job(TalusModel):
    """The model for running tasks ("Jobs")"""
    api_path = "api/job"
    interactive_create_command = "job create --shell"
    fields = {
        "name": Field("", desc="The name of the job"),
        "task": RefField("Task", desc="The task the job is based on"),
        "params": Field({}, details=True, desc="Parameters (inherited from the chosen task)"),
        "status": Field({}, desc="Status of the job (don't touch)"),
        "timestamps": Field({}, desc="Timestamps (don't touch)"),
        "queue": Field("", desc="The queue the job should be dripped into (normal use leave blank)"),
        "priority": Field(50, desc="Priority of the job (0-100, 100 == highest priority)", validation=lambda x: 0 <= x <= 100 ), # 0-100
        "limit": Field(1, desc="The limit for the task (when does it finish?)"),
        "progress": Field(desc="Current progress of the job (don't touch)"),
        "image": RefField("Image", desc="The image the job should run on"),
        "network": Field("whitelist", desc="The network whitelist (E.g. 'whitelist:DOMAIN_1,IP_2,DOMAIN_3,...')"),
        "debug": Field(False, desc="If the job should be run in debug mode (logs are always saved)"),
        "vm_max": Field(60*30, desc="Max vm duration (s) before being forcefully terminated"), # seconds
        "errors": Field([], desc="Errors the job has accumulated"),
        "logs": Field([], desc="Debug logs for this job"),
        "tags" : Field([], desc="Tags associated with this job")
    }

class Code(TalusModel):
    """The model for Tools/Components"""
    api_path = "api/code"
    interactive_create_command = "code create --shell"
    fields = {
        "name": Field("", desc="The name of the component/tool"),
        "type": Field("", desc="The type of component or tool"),
        "params": Field([], details=True, desc="Parameter type info (don't touch, pulled from code))"),
        "bases": Field([], desc="Bases of the component/tool class (don't touch, pulled from code)"),
        "desc": Field("", details=True, desc="The description of the component/tool (don't touch, pulled from code)"),
        "timestamps": Field({}, details=True, desc="Timestamps (don't touch)"),
        "tags" : Field([], desc="Tags associated with this code")
    }

class OS(TalusModel):
    """The model for OS API objects"""
    api_path = "api/os"
    interactive_create_command = "os create --shell"
    fields = {
        "name": Field("", desc="The name of the OS"),
        "version": Field("", desc="The version of the OS (E.g. '7' for Windows 7"),
        "type": Field("", desc="Only 'linux' or 'windows' are allowed", validation=lambda x: x in ["windows", "linux"]),
        "arch": Field("", desc="E.g. x64"),
        "tags" : Field([], desc="Tags associated with this OS")
    }
        
class Image(TalusModel):
    """The model for Image API objects"""
    api_path = "api/image"
    interactive_create_command = "image create --shell"
    fields = {
        "name": Field("", desc="The name of the image"),
        "os": RefField("OS", desc="The OS of the image"),
        "desc": Field("", details=True, desc="The desctription for the image"),
        "tags": Field([], desc="Tags for the image"),
        "status": Field({}, desc="The status for the image (don't touch)"),
        "base_image": RefField("Image", desc="The base image this image is based on"),
        "username": Field("user", details=True, desc="The username for the image"),
        "password": Field("password", details=True, desc="The password for the image"),
        "md5": Field("", desc="The md5 of the image (don't touch)"),
        "timestamps": Field({}, details=True, desc="Timestamps (don't touch)"),
    }

    def children(self):
        return self.objects(base_image = self.id)

class Result(TalusModel):
    """The model for Result objects"""
    api_path = "api/result"
    fields = {
        "job": RefField("Job", desc="The job that generated this result"),
        "type": Field("", desc="The result type"),
        # TODO is this used/needed?
        "tool": Field("", desc="The tool that was run"),
        "data": Field({}, desc="The result data"),
        "created": Field(desc="When it was created"),
        "tags" : Field([], desc="Tags associated with this result")
    }

class Master(TalusModel):
    """The model for Master API objects -- intended to be READ ONLY"""
    api_path = "api/master"
    fields = {
        "hostname": Field("", desc="The hostname of the master"),
        "ip": Field("", desc="The ip address of the master"),
        "vms": Field([], desc="A list of running vms on the master (for configuration)"),
        "queues": Field({}, desc="A dict of priority queues and their contents"),
    }

class Slave(TalusModel):
    """The model for Slave API objects -- intended to be READ ONLY"""
    api_path = "api/slave"
    fields = {
        "hostname": Field("", desc="The hostname of the slave"),
        "uuid": Field("", desc="The uuid of the slave"),
        "ip": Field("", desc="The ip address of the slave"),
        "max_vms": Field(1, desc="Maximum vms the slave is set to run at once"),
        "running_vms": Field(0, desc="Number of currently-running vms"),
        "total_jobs_run": Field(0, desc="Total jobs this slave has run"),
        "vms": Field([], desc="List of running vm information"),
    }

class FileSet(TalusModel):
    """The model for FileSet API objects"""
    api_path = "api/fileset"
    fields = {
        "name"            : Field("", desc="Name of the fileset"), 
        "files"            : Field([], desc="List of fileids associated with this fileset"),
        "timestamps"    : Field({}, desc="Timestamps for this fileset (don't touch)"),
        "job"            : RefField("Job", desc="Job that generated this fileset (not required)"),
        "tags"            : Field([], desc="Tags associated with this fileset")
    }
