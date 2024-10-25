# Lambda CLI

A command line tool for managing Lambda Labs instances with automatic file synchronization and environment management.

## Installation

### Option 1: Direct Installation from GitHub
```bash
pip install git+https://github.com/yourusername/lambda-cli.git
```

### Option 2: Run Directly
Download `lambda_cli.py` and run it directly - it will install its own dependencies if needed:
```bash
./lambda_cli.py [command]
```

## Usage

### Launch an Instance
```bash
# By default syncs current directory
lambda-cli launch gpu_1x_a100

# After launching via lambda-cli, you can also directly ssh or scp using 
# the default 'lambda' name
scp my_local_file.txt lambda:my_remote_file.txt

# With custom sync directory and ssh name
lambda-cli launch gpu_1x_a100 --name my-instance --sync-dir ./my-project

# With environment variables
lambda-cli launch gpu_1x_a100 -e WANDB_API_KEY=abc123 --env-file .env

```

### Reconnect to Running Instance
```bash
# Reconnect to default instance (by default syncs current directory)
lambda-cli ssh
```

### List Running Instances
```bash
lambda-cli list-running
```

### Shutdown an Instance
```bash
# Shutdown the default instance
lambda-cli shutdown 
# Shutdown a specific instance
lambda-cli shutdown my-instance
```

## Features

- Automatic instance type matching
- File synchronization 
    - TODO: .gitignore support
    - TODO: When reconnecting from different directory,
      still sync with original directory
    - TODO: Reverse sync. For now you still have to scp manually
- Environment variable management
- SSH config management
- Persistent configuration

## Requirements

- Python 3.6+
- SSH client
- rsync

Dependencies (automatically installed):
- click
- watchdog
- PyYAML
- requests