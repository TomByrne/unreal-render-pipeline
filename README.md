# AWS Unreal Render Pipeline
This is a tool for automating the rendering of Unreal sequences.
This is a work in progress.

## Usage
Run this to start the watcher:
```py
python ./watcher.py
```

Now, job files dropped into the `1_todo` will be processed, see the `example-job.json` file for the format (more info below).

To render multiple at once, put this checkout on a shared network drive and run the script on multiple PCs.

## Job Options

- **cmd** - The name of the command script that is used to start Unreal. Can be an absolute path or a file from the `cmds` folder. Can contain tokens (see below).
- **project**  - Absolute path to the Unreal project. Can target the `.unproject` file, or just the folder.
- **scene** - The scene file, as located within the project's `Content` folder.
- **sequence** - The sewquence file, as located within the project's `Content` folder.
- **render_settings** - The name of the Movie Render Queue settings file that configures the renderer. Can be an absolute path or a file from the `render_settings` folder. Can contain tokens (see below).
- **output** - Absolute path to output folder, can contain tokens (see below). Will default to `default_output_path` value in `watcher.py` if unspecified.
- **output_format** - Output file name, can contain tokens (see below). Will default to `default_output_format` value in `watcher.py` if unspecified. Can also contain single moustache syntax for Unreal's tokens.
- **attempts** - Total amount of render attempts, including the first, if something happens during export (crash/abort/etc). Will default to `default_attempts` value in `watcher.py` if unspecified (2).
- **width** - Explicitly control the width of the output frame. Will default to the `default_width` value in `watcher.py` if unspecified (1920).
- **height** - Explicitly control the height of the output frame. Will default to the `default_height` value in `watcher.py` if unspecified (1080).
- **scale** - Apply a scale factor to both the width and height of the output frame.

## Text file tokens
The `cmd` and `render_settings` filesare all the information provided to UE to do the render, so they must contain swappable tokens to allow the project/scene/sequence values to be sent through.

Tokens are in double moustache syntax:

- **{{project}}** - Absolute path to project's `.uproject` file.
- **{{scene}}** - Relative path to the scene asset within the Content folder.
- **{{scene_name}}** - Name of the scene asset (i.e. after the last '/').
- **{{sequence}}** - Relative path to the sequence asset within the Content folder.
- **{{sequence_name}}** - Name of the sequence asset (i.e. after the last '/').
- **{{date}}** - Datestamp in `"%Y.%m.%d"` format (e.g. 2022.02.31)
- **{{output_path}}** - Absolute path to output folder, with all tokens resolved.
- **{{output_format}}** - Output file name, with all tokens resolved.
- **{{start_frame}}** - The frame to begin exporting from, mostly used to restart failed exports.
- **{{custom_frame_range}}** - Integer boolean flag to indicate whether a custom start frame is in use.
- **{{render_settings}}** - Absolute path to the generated render settings file.
- **{{width}}** - Width of the output frame
- **{{height}}** - Height of the output frame
- **{{resolution_base64}}** - The dimensions of the output frame must be based through in the settings template encoded as base64.
