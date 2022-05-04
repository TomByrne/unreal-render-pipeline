from genericpath import exists
import glob
import time
import json
import os
import getpass
from subprocess import Popen, PIPE
import shlex
import base64
import hashlib
from pathlib import Path
import random
import datetime
import re
from dotenv import load_dotenv

load_dotenv()

jobs_path = os.path.abspath(os.getenv('PATH_JOBS') or "../jobs")
cancelled_path = os.path.abspath(os.getenv('PATH_CANCELLED') or "../jobs/cancelled")
done_path = os.path.abspath(os.getenv('PATH_DONE') or "../jobs/done")
error_path = os.path.abspath(os.getenv('PATH_FAILED') or "../jobs/failed")
settings_path = "render_settings"
cmd_path = "cmds"
settings_asset_path = "MovieRenderPipeline"
cmd_template = "cmd_template.ps1"

alivefile_suffix = "   [ {{worker}} ]   {{duration}}s {{frames}}f "
alivefile_timeout = 120 # secs

username = getpass.getuser()
username_safe = username.replace(" ", "_")
cwd = os.getcwd().replace("\\", "/")

dev_mode = os.getenv('DEV_MODE') == "1"

default_width = int(os.getenv('DEFAULT_WIDTH') or '1920')
default_height = int(os.getenv('DEFAULT_HEIGHT') or '1080')
default_attempts = int(os.getenv('DEFAULT_ATTEMPTS') or '2')
default_output_format = os.getenv('DEFAULT_OUT_FORMAT') or '{{scene_name}}/{{sequence_name}}/{{date}}/{sequence_name}.{frame_number}'
default_output_path  = os.getenv('DEFAULT_OUT_PATH') or os.path.abspath('../output')

print("---------------- \nUnreal RenderDrop v0.8 \n----------------")

print('Watching folder for jobs: "{}"'.format(jobs_path))

def mkdir(path):
    if not os.path.exists(path):
        Path(path).mkdir(parents=True, exist_ok=True)

# Create job folders
mkdir(jobs_path)
mkdir(cancelled_path)
mkdir(done_path)
mkdir(error_path)

def cleanup(path):
    if path != None and os.path.exists(path):
        os.remove(path)
        print('Cleanup file: {}'.format(path))
    else:
        print('Skipped cleanup: {} {}'.format(path, os.path.exists(path)))

def file_hash(path):
    path_read = open(path, "r")
    hash = hashlib.md5(path_read.read().encode("utf-8")).hexdigest()
    path_read.close()
    return hash

def json_escape(str):
    str = str.replace('\\', '\\\\')
    str = str.replace('"', '\\"')
    return str

while True:
    job_files = sorted(glob.glob("{}/*.json".format(jobs_path)))
    for file in job_files:
        job_file = file[len(jobs_path) + 1:len(file)]
        job_name = re.search('^(.*?)(_[\d]+)?.json$', job_file).group(1) # strip trailing '_0', for when job are re-added from complete folder

        alive_files = glob.glob("{}/{}*".format(jobs_path, job_name))
        alive_files = list(filter(lambda f : f != file and os.path.splitext(f)[1] != ".json", alive_files))
        alive_cutoff = time.time() - alivefile_timeout
        if len(alive_files) > 0:
            modtime = os.path.getmtime(alive_files[0])
            if modtime < alive_cutoff:
                print('Job alive file is more than {}s old, taking job: "{}"'.format(alivefile_timeout, file))
                os.remove(alive_files[0])
            else:
                # skip job already in progress
                continue
        else:
            print('Job found: "{}"'.format(file))

        date = datetime.date.today().strftime("%Y.%m.%d")

        id = "{}.{}.{:03d}".format(username_safe, job_name, random.randint(0, 999))

        setting_output_abs = None
        project = None
        
        def token_replace(value, start_frame=None, end_frame=None, settings=None):
            value = value.replace("{{worker}}", username_safe)
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

            custom_range = "1" if start_frame != None or end_frame != None else "0"
            value = value.replace("{{custom_frame_range}}", custom_range)

            start_frame = str(start_frame) if start_frame != None else "0"
            value = value.replace("{{start_frame}}", str(start_frame))

            end_frame = str(end_frame) if end_frame != None else "100000000"
            value = value.replace("{{end_frame}}", str(end_frame))

            if settings != None:
                value = value.replace("{{render_settings}}", settings)
            return value

        alive_file = None

        def update_alive(duration = 0, frames = 0):
            newalive = file + token_replace(alivefile_suffix)
            newalive = newalive.replace("{{duration}}", str(round(duration)))
            newalive = newalive.replace("{{frames}}", str(frames))
            if newalive == alive_file:
                Path(alive_file).touch()
                return newalive

            if alive_file != None and os.path.exists(alive_file):
                Path(alive_file).rename(newalive)
            else:
                open(newalive, 'a').close()

            return newalive

        def save_job(dir):
            path = "{}/{}.json".format(dir, job_name)
            count = 0
            while os.path.exists(path):
                path = "{}/{}_{}.json".format(dir, job_name, count)
                count += 1

            with open(path, 'w') as file_write:
                json.dump(data, file_write, indent=4)
        
        
        try:
            render = None
            
            alive_file = update_alive()

            file_read = open(file, "r")
            data = json.load(file_read)
            file_read.close()

            job_hash = file_hash(file)

            attempts = data.get("attempts")
            if attempts == None:
                attempts = default_attempts

            renders = data.get("renders")
            if renders == None or not isinstance(renders, list):
                renders = []
                data["renders"] = renders

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
                projfiles = glob.glob("{}/*.uproject".format(project))
                if len(projfiles) == 0:
                    raise Exception('Failed to find project file in : {}'.format(project))

                project = projfiles[0]
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

            # Start end frame
            start_frame = data.get('start_frame')
            end_frame = data.get('end_frame')
            total_frame = end_frame - start_frame if start_frame != None and end_frame != None else None
            
            ## Find output path
            output_path = data.get("output")
            if output_path == None:
                output_path = default_output_path

            output_path = json_escape(output_path)
            
            output_format = data.get("output_format")
            if output_format == None:
                output_format = default_output_format

            output_format = json_escape(output_format)
                
            output_path = token_replace(output_path)
            output_format = token_replace(output_format)

            output_dir = Path(output_path + output_format).parent
            print('Output folder: "{}"'.format(output_dir.absolute()))

            def get_output_files_since(time):
                try:
                    return len([f for f in os.listdir(output_dir) if os.path.getctime(os.path.join(output_dir, f)) > time])
                except:
                    return 0
                

            settings_configs = data.get('render_settings')
            if isinstance(settings_configs, str):
                settings_configs = [settings_configs]

            ## Loop over all configs and export each
            for setting_input in settings_configs:
                
                remaining_attempts = attempts
                last_render_start = time.time()
                
                render = {
                    "settings": setting_input,
                    "worker": username_safe,
                    "time": last_render_start,
                    "output": str(output_dir.absolute()),
                    "width": width,
                    "height": height,
                    "errors": [],
                    "outcome": None,
                }
                renders.append(render)

                while remaining_attempts > 0:

                    real_start_frame = start_frame
                    if setting_input != settings_configs[0]:
                        skip_frame = get_output_files_since(last_render_start)

                        if total_frame == None or skip_frame < total_frame:
                            real_start_frame = real_start_frame + skip_frame if real_start_frame != None else skip_frame
                            print("Resuming render from frame: {}".format(start_frame))

                    remaining_attempts = remaining_attempts - 1

                    ## Resolve settings file path
                    if not os.path.exists(setting_input):
                        setting_input = "{}/{}.json".format(settings_path, setting_input)

                    ## Load settings file
                    print('Loading render settings: "{}"'.format(setting_input))
                    with open(setting_input) as f:
                        settings_str = f.read()
                    
                    settings_str = token_replace(settings_str, real_start_frame, end_frame)
                    
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
                        
                    cmd_str = token_replace(cmd_str, real_start_frame, end_frame, setting_output)
                    cmd_str = cmd_str.replace("\n", " ")

                    print('Beginning export: {}'.format(cmd_str))
                    proc = Popen(shlex.split(cmd_str), stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, bufsize=10)
                    returncode = None
                    cancelled = False
                    while returncode == None:
                        returncode = proc.poll()
                        duration = time.time() - last_render_start
                        # frames = get_output_files_since(last_render_start)
                        frames = get_output_files_since(0)
                        render["duration"] = duration
                        render["frames_done"] = frames

                        if not os.path.exists(file) or job_hash != file_hash(file):
                            hash2 = file_hash(file)
                            proc.kill()
                            cancelled = True
                            break

                        if returncode == None:
                            alive_file = update_alive(duration, frames)
                            time.sleep(2)

                    if cancelled:
                        render["outcome"] = "cancelled"
                        print('Job file removed/changed, job cancelled: "{}"'.format(file))
                        save_job(cancelled_path)
                        remaining_attempts = 0

                    elif returncode == 0:
                        render["outcome"] = "success"
                        print('Export successful!!')
                        save_job(done_path)
                        remaining_attempts = 0

                    else:
                        render["errors"].append({
                            "code": returncode,
                            "stdout": proc.stdout.read(),
                            "stderr": proc.stderr.read()
                        })
                        render["outcome"] = "failed"
                        if remaining_attempts == 0 or not os.path.exists(output_dir):
                            print('Export failed, aborting!!!')
                            raise Exception("Failed")
                        else:
                            print('Export failed, retrying...')

        except Exception as e:
            print(str(e))
            print('Job failed!')
            save_job(error_path)

        if not dev_mode or (render != None and render["outcome"] != "failed"):
            cleanup(setting_output_abs)

        cleanup(alive_file)
        if os.path.exists(file) and job_hash == file_hash(file):
            cleanup(file)

        break # So that glob loop starts again
            

    time.sleep(5)