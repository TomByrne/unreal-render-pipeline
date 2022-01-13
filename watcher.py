from genericpath import exists
import glob
import time
import json
import os
import getpass
import subprocess
import shlex
import base64
import hashlib
from pathlib import Path
import random
import datetime

todo_path = "1_todo"
done_path = "3_done"
error_path = "4_failed"
settings_path = "render_settings"
cmd_path = "cmds"
settings_asset_path = "MovieRenderPipeline"
cmd_template = "cmd_template.ps1"

alivefile_suffix = ".{{worker}}"
alivefile_timeout = 120 # secs

username = getpass.getuser()
cwd = os.getcwd().replace("\\", "/")

if username == "tombyrne":
    todo_path = "1_todo_dev"

default_width = 1920
default_height = 1080
default_attempts = 2
default_output_format = '{{scene_name}}//{{sequence_name}}//{{date}}//{sequence_name}.{frame_number}'
default_output_path  = 'X://AWS_ReInvent_2021//GoogleDrive//Unreal_Output//'

print("---------------- \nUnreal RenderDrop v0.7 \n----------------")

print('Watching folder for jobs: "{}"'.format(todo_path))

def cleanup(path):
    if path != None and os.path.exists(path):
        os.remove(path)
        print('Cleanup file: {}'.format(path))

def file_hash(path):
    path_read = open(path, "r")
    hash = hashlib.md5(path_read.read().encode("utf-8")).hexdigest()
    path_read.close()
    return hash

while True:
    job_files = sorted(glob.glob("{}/*.json".format(todo_path)))
    for file in job_files:
        jobname = file[len(todo_path) + 1:len(file) - 5]
        alive_files = glob.glob("{}/{}*".format(todo_path, jobname))
        alive_files = list(filter(lambda f : f != file, alive_files))
        alive_cutoff = time.time() - alivefile_timeout
        if len(alive_files) > 0:
            modtime = os.path.getmtime(alive_files[0])
            if modtime < alive_cutoff:
                print('Job alive file is more than {}s old, taking job: "{}"'.format(alivefile_timeout, file))
            else:
                # skip job already in progress
                continue
        else:
            print('Job found: "{}"'.format(file))

        date = datetime.date.today().strftime("%Y.%m.%d")

        id = "{}.{}.{:03d}".format(username, jobname, random.randint(0, 999))

        setting_output_abs = None
        project = None
        
        def token_replace(value, start_frame=0, settings=None):
            value = value.replace("{{worker}}", username)
            value = value.replace("{{date}}", date)

            if project != None:
                value = value.replace("{{project}}", project)
                value = value.replace("{{scene}}", scene)
                value = value.replace("{{sequence}}", sequence)
                value = value.replace("{{scene_name}}", scene_name)
                value = value.replace("{{level_name}}", scene_name)
                value = value.replace("{{sequence_name}}", sequence_name)
            
                value = value.replace("{{output_path}}", output_path)
                value = value.replace("{{output_format}}", output_format)
                
                value = value.replace("{{width}}", str(width))
                value = value.replace("{{height}}", str(height))
                value = value.replace("{{resolution_base64}}", resolution_base64)

            if start_frame != None:
                value = value.replace("{{start_frame}}", str(start_frame))
                value = value.replace("{{custom_frame_range}}", str( 1 if start_frame != 0 else 0))

            if settings != None:
                value = value.replace("{{render_settings}}", settings)
            return value

        alivefile = None

        def update_alive():
            newalive = todo_path + "/" + jobname + token_replace(alivefile_suffix)
            if newalive == alivefile:
                Path(alivefile).touch()
                return newalive

            if alivefile != None and os.path.exists(alivefile):
                os.remove(alivefile)
            
            open(newalive, 'a').close()
            return newalive
        
        
        try:
            
            alivefile = update_alive()

            file_read = open(file, "r")
            data = json.load(file_read)
            file_read.close()

            job_hash = file_hash(file)

            attempts = data.get("attempts")
            if attempts == None:
                attempts = default_attempts

            width = data.get("width")
            if width == None:
                width = default_width
            height = data.get("height")
            if height == None:
                height = default_height

            scale = data.get("scale")
            if scale != None:
                width = round(width * scale)
                height = round(height * scale)

            resolution_bytes = width.to_bytes(4, byteorder="little") + height.to_bytes(4, byteorder="little")
            resolution_base64 = base64.b64encode(resolution_bytes).decode("utf-8")
                
            ## Resolve UE project file
            project = data.get('project')
            if project.find(".uproject") == -1:
                project = glob.glob("{}/*.uproject".format(project))[0]
                print('Found project file: "{}"'.format(project))

            project_dir = Path(project).parent.absolute()
            
            ## Resolve scene/sequence settings
            scene = data.get('scene')
            sequence = data.get('sequence')
            scene_name = scene[scene.rfind("/") + 1:len(scene)]
            sequence_name = sequence[sequence.rfind("/") + 1:len(sequence)]

            ## Validate scene asset exists
            scene_path = '{}/Content{}.umap'.format(project_dir, scene)#.replace('/', '\\')
            if not os.path.exists(scene_path):
                raise Exception('Failed to locate scene asset at: {}'.format(scene_path))

            ## Validate sequence asset exists
            sequence_path = '{}/Content{}.uasset'.format(project_dir, sequence)#.replace('\\', '/')
            if not os.path.exists(sequence_path):
                raise Exception('Failed to locate sequence asset: {}'.format(sequence_path))

            
            ## Find output path
            output_path = data.get("output")
            if output_path == None:
                output_path = default_output_path
            
            output_format = data.get("output_format")
            if output_format == None:
                output_format = default_output_format
                
            output_path = token_replace(output_path)
            output_format = token_replace(output_format)

            output_dir = Path(output_path + output_format).parent
            print('Output folder: "{}"'.format(output_dir.absolute()))

            def get_output_files_since(time):
                return [f for f in os.listdir(output_dir) if os.path.getmtime(os.path.join(output_dir, f)) > time]

            settings_configs = data.get('render_settings')
            if isinstance(settings_configs, str):
                settings_configs = [settings_configs]

            ## Loop over all configs and export each
            for setting_input in settings_configs:

                remaining_attempts = attempts
                last_render_start = time.time()

                while remaining_attempts > 0:

                    skip_frames = 0
                    if setting_input != settings_configs[0]:
                        skip_frames = len(get_output_files_since(last_render_start))
                        print("Resuming render from frame: {}".format(skip_frames))

                    remaining_attempts = remaining_attempts - 1

                    ## Resolve settings file path
                    if not os.path.exists(setting_input):
                        setting_input = "{}/{}.json".format(settings_path, setting_input)

                    ## Load settings file
                    print('Loading render settings: "{}"'.format(setting_input))
                    with open(setting_input) as f:
                        settings_str = f.read()
                    
                    settings_str = token_replace(settings_str, skip_frames)
                    
                    ## Save settings file into project
                    setting_output = "{}/{}.utxt".format(settings_asset_path, id.replace(".", "_"))
                    setting_output_abs = "{}/Saved/{}".format(project_dir, setting_output)

                    ## Create `MovieRenderPipeline` dir if it doesn't exist
                    Path(setting_output_abs).parent.mkdir(parents=True, exist_ok=True)

                    print('Saving render settings: "{}"'.format(setting_output_abs))
                    with open(setting_output_abs, 'w') as f:
                        f.write(settings_str)
                    
                    ## Resolve CMD file
                    cmd_input = data.get('cmd')
                    if not os.path.exists(cmd_input):
                        cmd_input = "{}/{}.ps1".format(cmd_path, cmd_input)
                    print('Loading cmd: "{}"'.format(cmd_input))

                    with open(cmd_input) as f:
                        cmd_str = f.read()
                        
                    cmd_str = token_replace(cmd_str, skip_frames, setting_output)
                    cmd_str = cmd_str.replace("\n", " ")

                    print('Beginning export: {}'.format(cmd_str))
                    proc = subprocess.Popen(shlex.split(cmd_str))
                    returncode = None
                    cancelled = False
                    while returncode == None:
                        returncode = proc.poll()

                        if not os.path.exists(file) or job_hash != file_hash(file):
                            hash2 = file_hash(file)
                            proc.kill()
                            cancelled = True
                            break

                        if returncode == None:
                            alivefile = update_alive()
                            time.sleep(3)

                    if cancelled:
                        print('Job file removed/changed, job cancelled: "{}"'.format(file))
                    elif returncode == 0:
                        done_file = "{}/{}.json".format(done_path, id)
                        print('Export successful!!')
                        print('Moving job file to {}'.format(done_file))
                        os.rename(file, done_file)
                        remaining_attempts = 0
                    else:
                        if remaining_attempts == 0 or not os.path.exists(output_dir):
                            print('Export failed, aborting!!!')
                            raise Exception("Failed")
                        else:
                            print('Export failed, retrying...')

        except Exception as e:
            print(str(e))
            failed_file = "{}/{}.json".format(error_path, id)
            print('Job failed, moving to: "{}"'.format(failed_file))
            os.rename(file, failed_file)
            Path(failed_file).touch() # update modified date/time

        cleanup(setting_output_abs)
        cleanup(alivefile)

        break # So that glob loop starts again
            

    time.sleep(5)