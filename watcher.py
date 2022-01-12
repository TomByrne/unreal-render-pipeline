from genericpath import exists
import glob
import time
import json
import os
import getpass
import subprocess
import shlex
from pathlib import Path
import random
import datetime

todo_path = "1_todo"
doing_path = "2_doing"
done_path = "3_done"
error_path = "4_failed"
settings_path = "render_settings"
cmd_path = "cmds"
settings_asset_path = "MovieRenderPipeline"
cmd_template = "cmd_template.ps1"

username = getpass.getuser()
cwd = os.getcwd().replace("\\", "/")

if username == "tombyrne":
    todo_path = "1_todo_dev"

default_attempts = 2
default_output_format = '{{level_name}}//{{sequence_name}}//{{date}}//{sequence_name}.{frame_number}'
default_output_path  = 'X://AWS_ReInvent_2021//GoogleDrive//Unreal_Output//'

print("---------------- \nUnreal RenderDrop v0.6 \n----------------")

print('Watching folder for jobs: "{}"'.format(todo_path))

while True:
    for file in glob.glob("{}/*.json".format(todo_path)):

        jobname = file[len(todo_path) + 1:len(file) - 5]

        date = datetime.date.today().strftime("%Y.%m.%d")

        print('Job found: "{}"'.format(file))

        id = "{}.{}.{:03d}".format(username, jobname, random.randint(0, 999))

        setting_output_abs = None
        
        try:
            ## Move file to 'doing' folder
            doing_file = "{}/{}.json".format(doing_path, id)
            print('Moving job to doing: "{}"'.format(doing_file))
            os.rename(file, doing_file)
            Path(doing_file).touch() # update modified date/time
            print("Move success")
            file = doing_file

            with open(file, "r") as read_file:
                data = json.load(read_file)

            attempts = data.get("attempts")
            if attempts == None:
                attempts = default_attempts

                
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


            def token_replace(value, start_frame=0, settings=None):
                value = value.replace("{{scene}}", scene)
                value = value.replace("{{sequence}}", sequence)
                value = value.replace("{{scene_name}}", scene_name)
                value = value.replace("{{level_name}}", scene_name)
                value = value.replace("{{sequence_name}}", sequence_name)
                value = value.replace("{{date}}", date)
                value = value.replace("{{project}}", project)
                
                value = value.replace("{{output_path}}", output_path)
                value = value.replace("{{output_format}}", output_format)

                if start_frame != None:
                    value = value.replace("{{start_frame}}", str(start_frame))
                    value = value.replace("{{custom_frame_range}}", str( 1 if start_frame != 0 else 0))

                if settings != None:
                    value = value.replace("{{render_settings}}", settings)
                return value

            
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

                    print('Beginning export: {}'.format(cmd_str))
                    # last_render_start = time.time()
                    res = subprocess.run(shlex.split(cmd_str), stdout=subprocess.PIPE)

                    if res.returncode == 0:
                        print('Export successful!!')
                        done_file = "{}/{}.json".format(done_path, id)
                        os.rename(file, done_file)
                        Path(done_file).touch() # update modified date/time
                        remaining_attempts = 0
                    else:
                        if remaining_attempts == 0 or not os.path.exists(output_dir):
                            print('Export failed, aborting!!!')
                            raise "Oops"
                        else:
                            print('Export failed, retrying...')

        except Exception as e:
            print(str(e))
            failed_file = "{}/{}.json".format(error_path, id)
            print('Job failed, moving to: "{}"'.format(failed_file))
            os.rename(file, failed_file)
            Path(failed_file).touch() # update modified date/time

        if setting_output_abs != None and os.path.exists(setting_output_abs):
            os.remove(setting_output_abs)
            

        break # So that glob loop starts again
            

    time.sleep(5)