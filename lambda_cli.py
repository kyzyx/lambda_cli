#!/usr/bin/env python3
"""
Lambda CLI - A command line tool for managing Lambda Labs instances

Installation:
    pip install git+https://github.com/yourusername/lambda-cli.git

Or directly run this file - it will install its own dependencies if needed.
"""
import sys
import subprocess
from pathlib import Path

# Check and install dependencies if needed
def ensure_dependencies():
    try:
        import click
        import watchdog
        import yaml
    except ImportError:
        print("Installing required dependencies...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "click>=8.0.0",
            "watchdog>=2.1.0",
            "PyYAML>=5.4.1"
        ])
        print("Dependencies installed successfully!")

        # Re-import after installation
        import click
        import watchdog
        import yaml

# Only try to import dependencies after ensuring they're installed
if __name__ == "__main__":
    ensure_dependencies()

import yaml
import click
import requests
import time
import os
import json
import threading
import re
import tempfile
from typing import List, Dict, Optional, Tuple
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileDeletedEvent, FileMovedEvent


class Config:
    def __init__(self):
        self.config_dir = os.path.expanduser("~/.config/lambda-cli")
        self.config_file = os.path.join(self.config_dir, "config.yaml")
        self.config = self.load_config()

    def load_config(self) -> dict:
        """Load configuration, performing first-time setup if needed"""
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)

        if not os.path.exists(self.config_file):
            return self.initial_setup()

        with open(self.config_file, 'r') as f:
            return yaml.safe_load(f) or {}

    def initial_setup(self) -> dict:
        """Perform first-time setup and create config file"""
        click.echo("Welcome to Lambda CLI! Let's set up your configuration.")
        ssh_keyname = click.prompt(
            "\nEnter the name of your SSH key on Lambda",
            type=str
        )
        
        env_file = click.prompt(
            "\nEnter path to default environment file (optional, press enter to skip)",
            type=str,
            default="",
            show_default=False
        )

        config = {
            'ssh_keyname': ssh_keyname,
            'defaults': {
                'instance_name': 'lambda',
                'forward_ports': [],  # Default empty list for port forwarding
            }
        }
        
        if env_file:
            config['env_file'] = env_file

        self.save_config(config)
        return config

    def save_config(self, config: dict):
        """Save configuration to file"""
        with open(self.config_file, 'w') as f:
            yaml.safe_dump(config, f)

    def get_ssh_keyname(self) -> str:
        """Get SSH key path, running setup if not configured"""
        if 'ssh_keyname' not in self.config:
            self.config['ssh_keyname'] = click.prompt(
                "\nEnter the name of your SSH key on Lambda",
                type=str
            )
            self.save_config(self.config)

        return self.config['ssh_keyname']

    def get_default(self, key: str, default: any = None) -> any:
        """Get a default value from config"""
        return self.config.get('defaults', {}).get(key, default)

    def get_env_file(self) -> Optional[str]:
        """Get the default environment file path from config"""
        env_file = self.config.get('env_file')
        if env_file:
            # Expand ~ to user's home directory
            return os.path.expanduser(env_file)
        return None


class LambdaAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://cloud.lambdalabs.com/api/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def get_instance_types(self) -> dict:
        """Get all available instance types and their specifications"""
        url = f"{self.base_url}/instance-types"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to get instance types: {response.text}")
        return response.json()

    def launch_instance(self, instance_type, ssh_keynames, region_name=None):
        url = f"{self.base_url}/instance-operations/launch"
        payload = {
            "instance_type_name": instance_type,
            "region_name": region_name,
            "quantity": 1,
            "ssh_key_names": ssh_keynames,
        }
        if not region_name:
            del payload["region_name"]
            
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code != 200:
            print(f"Failed to launch instance: {response.text}")
            return None
        return response.json()

    def get_instance(self, instance_id):
        url = f"{self.base_url}/instances/{instance_id}"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to get instance info: {response.text}")
        return response.json()

    def terminate_instance(self, instance_id: str) -> dict:
        """Terminate a specific instance"""
        url = f"{self.base_url}/instance-operations/terminate"
        payload = {
            "instance_ids": [instance_id]
        }
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code != 200:
            raise Exception(f"Failed to terminate instance: {response.text}")
        return response.json()

    def list_instances(self) -> dict:
        """Get all running instances"""
        url = f"{self.base_url}/instances"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to get instances: {response.text}")
        return response.json()

    def get_instance_by_ip(self, ip: str) -> Optional[dict]:
        """Find instance by its name tag"""
        instances = self.list_instances()
        for instance in instances.get("data", {}):
            if instance.get("ip") == ip:
                return {"instance_id": instance.get("id"), **instance}
        return None

    def get_instance_by_name(self, name: str) -> Optional[dict]:
        """Find instance by its name tag"""
        instances = self.list_instances()
        for instance in instances.get("data", {}):
            if instance.get("name") == name:
                return {"instance_id": instance.get("id"), **instance}
        return None


def find_available_instance(api: LambdaAPI, desired_type: str) -> Tuple[str, str]:
    """Find an available instance type and region matching the desired specification"""
    instance_types = api.get_instance_types()
    
    # Find all instance types matching the desired specification
    matching_types = []
    for instance_type, info in instance_types["data"].items():
        if re.match(f"^{desired_type}.*", instance_type):
            matching_types.append((instance_type, info))

    if not matching_types:
        click.echo(f"No instance types found matching '{desired_type}'")
        click.echo("Available instance types:")
        for itype in instance_types["data"].keys():
            click.echo(f"  - {itype}")
        raise click.Abort()

    # Check availability for each matching type
    for instance_type, info in matching_types:
        # Find regions with available instances
        available_regions = [
            region['name']
            for region in info["regions_with_capacity_available"]
        ]

        if available_regions:
            # For now, just take the first available region
            # Could be enhanced to consider other factors like pricing
            return instance_type, available_regions[0]

    raise Exception(
        f"No available instances found matching '{desired_type}'. "
        "Try again later or specify a different instance type."
    )

def setup_ssh_configs():
    """Set up SSH config directory and Lambda-specific config file"""
    ssh_dir = os.path.expanduser("~/.ssh")
    main_config = os.path.join(ssh_dir, "config")
    lambda_config = os.path.join(ssh_dir, "lambda_config")

    # Create .ssh directory if it doesn't exist
    if not os.path.exists(ssh_dir):
        os.makedirs(ssh_dir, mode=0o700)

    # Create main config if it doesn't exist
    if not os.path.exists(main_config):
        with open(main_config, 'w') as f:
            f.write("# SSH Config File\n")
        os.chmod(main_config, 0o600)

    # Check if lambda_config is included in main config
    include_line = f"Include {lambda_config}"
    with open(main_config, 'r') as f:
        if include_line not in f.read():
            # Add include at the beginning of the file
            with open(main_config, 'r') as f:
                content = f.read()
            with open(main_config, 'w') as f:
                f.write(f"{include_line}\n\n{content}")

    # Create lambda config if it doesn't exist
    if not os.path.exists(lambda_config):
        with open(lambda_config, 'w') as f:
            f.write("# Lambda Labs SSH Config\n")
        os.chmod(lambda_config, 0o600)

    return lambda_config

def setup_ssh_config(instance_name, hostname, username="ubuntu"):
    """Set up SSH config entry in Lambda-specific config file"""
    lambda_config = setup_ssh_configs()

    new_entry = f"""
Host {instance_name}
    HostName {hostname}
    User {username}
    IdentityFile ~/.ssh/id_rsa
"""

    # Read existing lambda config
    with open(lambda_config, 'r') as f:
        lines = f.readlines()

    # Remove existing entry for this host if it exists
    new_lines = []
    skip_until_next_host = False
    for line in lines:
        if line.strip().startswith('Host '):
            if line.strip() == f'Host {instance_name}':
                skip_until_next_host = True
                continue
            else:
                skip_until_next_host = False
        if not skip_until_next_host:
            new_lines.append(line)

    # Add the new entry
    with open(lambda_config, 'w') as f:
        f.writelines(new_lines)
        f.write(new_entry)

def read_ssh_config(ssh_name: str) -> Optional[str]:
    """Get hostname/IP from Lambda SSH config for a given host entry"""
    lambda_config = os.path.join(os.path.expanduser("~/.ssh"), "lambda_config")
    if not os.path.exists(lambda_config):
        return None

    try:
        with open(lambda_config, 'r') as f:
            lines = f.readlines()

        current_host = None
        for line in lines:
            line = line.strip()
            if line.startswith('Host '):
                current_host = line.split()[1]
            elif line.startswith('HostName ') and current_host == ssh_name:
                return line.split()[1]
    except Exception as e:
        click.echo(f"Error reading SSH config: {e}", err=True)
        return None

    return None

def remove_ssh_config(ssh_name: str):
    """Remove an entry from Lambda SSH config"""
    lambda_config = os.path.join(os.path.expanduser("~/.ssh"), "lambda_config")
    if not os.path.exists(lambda_config):
        return

    # Read existing config
    with open(lambda_config, 'r') as f:
        lines = f.readlines()

    # Remove entry for this host
    new_lines = []
    skip_until_next_host = False
    for line in lines:
        if line.strip().startswith('Host '):
            if line.strip() == f'Host {ssh_name}':
                skip_until_next_host = True
                continue
            else:
                skip_until_next_host = False
        if not skip_until_next_host:
            new_lines.append(line)

    # Write back the config without the removed entry
    with open(lambda_config, 'w') as f:
        f.writelines(new_lines)

def get_instance_ip(api, instance_id, timeout=600):
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise Exception("Timeout waiting for instance to be ready")
            
        instance_info = api.get_instance(instance_id)
        if "ip" in instance_info["data"] and len(instance_info["data"]["ip"]) > 0:
            ip = instance_info["data"]["ip"]
            return ip
        time.sleep(5)

def wait_for_instance(api, instance_id, timeout=600):
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise Exception("Timeout waiting for instance to be ready")

        instance_info = api.get_instance(instance_id)
        status = instance_info["data"]["status"]
        
        if status == "active":
            return instance_info
        elif status == "failed":
            raise Exception("Instance failed to start")
            
        click.echo(f"Instance status: {status}")
        time.sleep(5)

def wait_for_ssh(host, timeout=300):
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise Exception("Timeout waiting for SSH to be ready")
            
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", 
             host, "echo 'SSH ready'"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return
            
        click.echo("Waiting for SSH to be ready...")
        time.sleep(5)

class SyncEventHandler(FileSystemEventHandler):
    def __init__(self, src_path: str, host: str, remote_path: str):
        self.src_path = os.path.abspath(src_path)
        self.host = host
        self.remote_path = remote_path
        self.last_sync = {}  # Track last sync time for each file
        self.sync_delay = 1.0  # Delay in seconds to prevent rapid successive syncs
        self._sync_lock = threading.Lock()
        self._pending_syncs = set()
        self._pending_deletes = set()
        self._sync_timer = None

    def on_any_event(self, event):
        # Ignore directory events and hidden files/directories
        if event.is_directory or any(p.startswith('.') for p in event.src_path.split(os.sep)):
            return

        # Ignore certain file patterns
        if any(pattern in event.src_path for pattern in [
            '__pycache__',
            '.DS_Store',
            '.git/',
        ]):
            return
        if any(event.src_path.endswith(pattern) for pattern in [
            '.pyc',
            '.swp'
            '.swo',
            '~',
            '4913',
            '.env'
        ]):
            return
        try:
            rel_path = os.path.relpath(event.src_path, self.src_path)
            with self._sync_lock:
                if isinstance(event, (FileDeletedEvent, FileMovedEvent)) and \
                   not os.path.exists(event.src_path):
                    # If file was deleted or moved away, add to pending deletes
                    self._pending_deletes.add(rel_path)
                    # Remove from pending syncs if it was there
                    self._pending_syncs.discard(rel_path)
                else:
                    # For created, modified, or moved-to events
                    self._pending_syncs.add(rel_path)
                    # Remove from pending deletes if it was there
                    self._pending_deletes.discard(rel_path)

                # Reset or start the sync timer
                if self._sync_timer is not None:
                    self._sync_timer.cancel()
                self._sync_timer = threading.Timer(self.sync_delay, self._sync_pending_files)
                self._sync_timer.start()

        except ValueError as e:
            click.echo(f"Error processing path {event.src_path}: {e}", err=True)

    def _sync_pending_files(self):
        """Sync all pending files and handle deletions in a single rsync command"""
        with self._sync_lock:
            try:
                # Handle deleted files
                if self._pending_deletes:
                    delete_list_file = None
                    try:
                        # Create a temporary file with the list of files to delete
                        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                            for rel_path in self._pending_deletes:
                                f.write(f"{rel_path}\n")
                            delete_list_file = f.name

                        # Use --delete-from to specifically delete these files
                        cmd = [
                            "rsync",
                            "-av",
                            "--delete",
                            "--existing",  # only update existing files
                            "--include-from=" + delete_list_file,
                            "--exclude=*",  # exclude everything else
                            #"--ignore-missing-args",
                            "/dev/null/",  # empty source directory
                            f"{self.host}:{self.remote_path}/"
                        ]

                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True
                        )

                        if result.returncode not in (0, 23):
                            click.echo(f"Warning: Delete sync had issues: {result.stderr}", err=True)
                        else:
                            num_files = len(self._pending_deletes)
                            # click.echo(f"Deleted {num_files} file{'s' if num_files != 1 else ''}")

                    finally:
                        if delete_list_file:
                            try:
                                os.unlink(delete_list_file)
                            except OSError:
                                pass
                        self._pending_deletes.clear()

                # Handle file updates
                if self._pending_syncs:
                    files_list_file = None
                    try:
                        # Create a temporary file with the list of files to sync
                        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                            for rel_path in self._pending_syncs:
                                f.write(f"{rel_path}\n")
                            files_list_file = f.name

                        # Sync all pending files
                        cmd = [
                            "rsync",
                            "-av",
                            "--progress",
                            "--delete",  # ensure deletions are propagated
                            "--files-from=" + files_list_file,
                            #"--ignore-missing-args",
                            "--partial",
                            self.src_path + "/",
                            f"{self.host}:{self.remote_path}"
                        ]

                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True
                        )

                        if result.returncode not in (0, 23):
                            click.echo(f"Warning: Sync had issues: {result.stderr}", err=True)
                        else:
                            num_files = len(self._pending_syncs)
                            #click.echo(f"Synced {num_files} file{'s' if num_files != 1 else ''}")

                    finally:
                        if files_list_file:
                            try:
                                os.unlink(files_list_file)
                            except OSError:
                                pass
                        self._pending_syncs.clear()

            except Exception as e:
                click.echo(f"Error during sync: {e}", err=True)

    def __del__(self):
        if self._sync_timer is not None:
            self._sync_timer.cancel()

def start_file_watcher(src_path: str, host: str, remote_path: str) -> Observer:
    """Start watching for file changes using watchdog"""
    click.echo("Starting file watcher...")
    
    event_handler = SyncEventHandler(src_path, host, remote_path)
    observer = Observer()
    observer.schedule(event_handler, src_path, recursive=True)
    observer.start()
    return observer

def sync_directory(src_path, host, remote_path):
    """Perform initial rsync of the directory"""
    cmd = [
        "rsync", "-av", "--progress",
        "--exclude", ".git",
        "--exclude", "*.pyc",
        "--exclude", "*.swo",
        "--exclude", "*.swp",
        "--exclude", "*~",
        "--exclude", "__pycache__",
        "--exclude", "*.DS_Store",
        "--exclude", ".env",
        f"{src_path}/",
        f"{host}:{remote_path}"
    ]
    subprocess.run(cmd, check=True)

def setup_environment(host: str, env_vars: Dict[str, str]):
    """Set up environment variables on the remote instance"""
    # Create a file for lambda-specific environment variables
    env_file = ".lambda_env"

    # Create content for the environment file
    env_content = "# Environment variables set by lambda-cli\n"
    for key, value in env_vars.items():
        # Escape special characters in the value
        escaped_value = value.replace('"', '\\"')
        env_content += f'export {key}="{escaped_value}"\n'

    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
        temp_file.write(env_content)
        local_file = temp_file.name

    try:
        # Upload the file using scp
        remote_env_file = "~/.lambda_env"
        subprocess.run(["scp", local_file, f"{host}:{remote_env_file}"], check=True)

        # Add source command to .bashrc if not already present
        source_line = f'\n# Source lambda environment variables\n[ -f {remote_env_file} ] && source {remote_env_file}\n'
        check_cmd = f'''ssh {host} 'grep -q "{remote_env_file}" ~/.bashrc || echo "{source_line}" >> ~/.bashrc' '''
        subprocess.run(check_cmd, shell=True, check=True)

    finally:
        # Clean up the temporary file
        os.unlink(local_file)

def parse_env_vars(env_list: List[str]) -> Dict[str, str]:
    """Parse environment variables from the format KEY=VALUE"""
    env_vars = {}
    for env_var in env_list:
        try:
            key, value = env_var.split('=', 1)
            if not key:
                raise ValueError("Empty key is not allowed")
            env_vars[key] = value
        except ValueError as e:
            raise click.BadParameter(
                f"Invalid environment variable format: {env_var}. "
                "Use KEY=VALUE format."
            ) from e
    return env_vars

def connect(name, sync_dir, remote_dir, env_vars=None, no_sync=True, forward_ports=None):
    ssh_args = ["ssh"]
    
    # Add port forwarding if specified
    if forward_ports:
        for port in forward_ports:
            ssh_args.extend(["-L", f"{port}:localhost:{port}"])
    
    ssh_args.append(name)
    
    if no_sync:
        subprocess.run(ssh_args)
        return

    click.echo("Performing initial file sync...")
    sync_directory(sync_dir, name, remote_dir)
    
    click.echo("Starting file watcher for continuous sync...")
    observer = start_file_watcher(sync_dir, name, remote_dir)
    
    click.echo(f"\nConnecting to instance...")
    try:
        # Start SSH session with environment variables
        if env_vars:
            setup_environment(name, env_vars)
        subprocess.run(ssh_args)
    except KeyboardInterrupt:
        click.echo("\nDisconnecting...")
    finally:
        # Cleanup
        click.echo("Stopping file watcher...")
        observer.stop()
        observer.join()

@click.group()
def cli():
    """CLI wrapper for LambdaLabs API"""
    pass

@cli.command()
@click.option('--instance-type', default="gpu_1x_a100", help='Instance type to launch')
@click.option('--region', help='Region to launch in (optional)')
@click.option('--name', help='Name for SSH alias')
@click.option('--api-key', envvar='LAMBDA_API_KEY', help='LambdaLabs API key')
@click.option('--sync-dir', help='Directory to sync to remote instance', 
              default='.', type=click.Path(exists=True))
@click.option('--remote-dir', help='Remote directory for syncing',
              default='~/')
@click.option('--env', '-e', multiple=True, help='Environment variables in KEY=VALUE format')
@click.option('--env-file', type=click.Path(exists=True),
              help='File containing environment variables (one KEY=VALUE per line)')
@click.option('--no-sync', is_flag=True, help="Don't sync any directory")
@click.option('--forward', type=int, multiple=True, help='Port(s) to forward from remote to local machine (can be specified multiple times)')
def launch(instance_type, region, name, api_key, sync_dir, remote_dir, env, env_file, no_sync, forward):
    """Launch a new instance, configure SSH access, sync files, and connect"""
    if not api_key:
        click.echo("Please set LAMBDA_API_KEY environment variable or provide --api-key")
        sys.exit(1)

    api = LambdaAPI(api_key)

    config = Config()
    if not name:
        name = config.get_default('instance_name', 'lambda')

    # Collect environment variables from both --env and --env-file
    env_vars = {}

    # Parse --env options
    if env:
        env_vars.update(parse_env_vars(env))

    # Parse --env-file if provided, falling back to config if not specified
    config = Config()
    if not env_file:
        env_file = config.get_env_file()
    
    if env_file:
        try:
            with open(env_file) as f:
                file_vars = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                env_vars.update(parse_env_vars(file_vars))
        except FileNotFoundError:
            click.echo(f"Warning: Environment file '{env_file}' not found", err=True)
        except Exception as e:
            click.echo(f"Warning: Error reading environment file: {e}", err=True)
    
    # If no region specified, find an available instance type and region
    if not region:
        click.echo("Finding available instance...")
        try:
            instance_type, region = find_available_instance(api, instance_type)
        except Exception as e:
            print(str(e))
            return
        click.echo(f"Selected instance type '{instance_type}' in region '{region}'")
    
    click.echo("Launching instance...")
    response = api.launch_instance(instance_type, [config.get_ssh_keyname()], region)
    if response is None:
        return
    instance_id = response["data"]["instance_ids"][0]
    
    click.echo("Setting up SSH config...")
    ip_address = get_instance_ip(api, instance_id)
    setup_ssh_config(name, ip_address)
    
    click.echo("Waiting for instance to be ready...")
    instance_info = wait_for_instance(api, instance_id)

    click.echo("Waiting for SSH to be ready...")
    wait_for_ssh(name)
    
    if not no_sync:
        click.echo("Creating remote directory...")
        subprocess.run(["ssh", name, f"mkdir -p {remote_dir}"], check=True)

    # Combine CLI-specified ports with defaults from config
    config = Config()
    default_ports = config.config.get('defaults', {}).get('forward_ports', [])
    all_ports = list(forward) + default_ports

    connect(name, sync_dir, remote_dir, env_vars, no_sync, all_ports)

@cli.command()
@click.option('--name', help='Name for SSH alias')
@click.option('--sync-dir', help='Directory to sync to remote instance', 
              default='.', type=click.Path(exists=True))
@click.option('--remote-dir', help='Remote directory for syncing',
              default='~/')
@click.option('--env', '-e', multiple=True, help='Environment variables in KEY=VALUE format')
@click.option('--env-file', type=click.Path(exists=True),
              help='File containing environment variables (one KEY=VALUE per line)')
@click.option('--no-sync', is_flag=True, help="Don't sync any directory")
@click.option('--forward', type=int, multiple=True, help='Port(s) to forward from remote to local machine (can be specified multiple times)')
def ssh(name, sync_dir, remote_dir, env, env_file, no_sync, forward):
    """Connect to a lambda instance (with file sync)"""
    if not name:
        config = Config()
        name = config.get_default('instance_name', 'lambda')

    # Collect environment variables from both --env and --env-file
    env_vars = {}

    # Parse --env options
    if env:
        env_vars.update(parse_env_vars(env))

    # Parse --env-file if provided
    if env_file:
        with open(env_file) as f:
            file_vars = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            env_vars.update(parse_env_vars(file_vars))

    # Combine CLI-specified ports with defaults from config
    config = Config()
    default_ports = config.config.get('defaults', {}).get('forward_ports', [])
    all_ports = list(forward) + default_ports if forward else default_ports

    connect(name, sync_dir, remote_dir, env_vars, no_sync, all_ports)

@cli.command()
@click.option('--api-key', envvar='LAMBDA_API_KEY', help='LambdaLabs API key')
@click.option('--show-all', is_flag=True, help='Show all instance types (including unavailable)')
def list_types(api_key, show_all):
    """List all available instance types and their current availability"""
    if not api_key:
        click.echo("Please set LAMBDA_API_KEY environment variable or provide --api-key")
        sys.exit(1)

    api = LambdaAPI(api_key)
    instance_types = api.get_instance_types()

    click.echo("\nAvailable instance types:")
    for itype, info in instance_types["data"].items():
        if show_all or len(info["regions_with_capacity_available"]) > 0:
            click.echo(f"\n{itype}:")
            click.echo(f"  Specs:")
            click.echo(f"    RAM: {info['instance_type']['specs']['memory_gib']} GB")
            click.echo(f"    GPUs: {info['instance_type']['specs'].get('gpus', 0)}")
            if info.get('gpu_description'):
                click.echo(f"    GPU Type: {info['instance_type']['gpu_description']}")

            click.echo("  Availability:")
            if info["regions_with_capacity_available"]:
                for region_info in info["regions_with_capacity_available"]:
                    region = region_info['name']
                    status = "✓ Available"
                    click.echo(f"    {region}: {status}")
            else:
                click.echo("    No instances currently available in any region")

        if "price_cents_per_hour" in info:
            click.echo(f"  Price: ${info['price_cents_per_hour']/100:.2f}/hour")

@cli.command()
@click.option('--ssh-name')
@click.option('--api-key', envvar='LAMBDA_API_KEY', help='LambdaLabs API key')
@click.option('--force', is_flag=True, help='Do not prompt for confirmation')
def shutdown(ssh_name, api_key, force):
    """Shutdown a running instance by name"""
    if not api_key:
        click.echo("Please set LAMBDA_API_KEY environment variable or provide --api-key")
        sys.exit(1)

    api = LambdaAPI(api_key)

    if not ssh_name:
        config = Config()
        ssh_name = config.get_default('instance_name', 'lambda')

    # Find instance by name
    ip = read_ssh_config(ssh_name)
    if not ip:
        click.echo(f"No SSH config entry found for '{ssh_name}'")
        return

    instance = api.get_instance_by_ip(ip)
    if not instance:
        click.echo(f"No running instance found with ip '{ip}'")

        # List available instances
        instances = api.list_instances()
        if instances.get("data"):
            click.echo("\nRunning instances:")
            for inst in instances["data"]:
                inst_ip = inst.get("ip", "0.0.0.0")
                inst_id = inst.get("id", "[Unknown]")
                click.echo(f"  - {inst_ip} ({inst_id})")
        else:
            click.echo("No instances are currently running")
        return

    # Confirm unless --force is used
    if not force:
        if not click.confirm(f"Are you sure you want to terminate instance '{ip}'?"):
            click.echo("Shutdown cancelled")
            return

    try:
        click.echo(f"Shutting down instance '{ip}'...")
        api.terminate_instance(instance["instance_id"])
        click.echo("Shutdown successful")

        # Clean up SSH config
        ssh_config_path = os.path.expanduser("~/.ssh/config")
        if os.path.exists(ssh_config_path):
            setup_ssh_config(ssh_name, "deleted-instance", "deleted")  # This will remove the entry

    except Exception as e:
        click.echo(f"Error during shutdown: {e}", err=True)

@cli.command()
@click.option('--api-key', envvar='LAMBDA_API_KEY', help='LambdaLabs API key')
def list_running(api_key):
    """List all running instances"""
    if not api_key:
        click.echo("Please set LAMBDA_API_KEY environment variable or provide --api-key")
        sys.exit(1)

    api = LambdaAPI(api_key)
    instances = api.list_instances()
    
    if not instances.get("data"):
        click.echo("No instances are currently running")
        return
        
    click.echo("\nRunning instances:")
    for instance in instances["data"]:
        name = instance.get("name", "unnamed")
        instance_id = instance.get("id", "[unknown]")
        instance_type = instance.get("instance_type", {}).get("name", "unknown")
        region = instance.get("region", {}).get("name", "unknown")
        ip = instance.get("ip", "no-ip")
        
        click.echo(f"\n{name}:")
        click.echo(f"  ID: {instance_id}")
        click.echo(f"  Type: {instance_type}")
        click.echo(f"  Region: {region}")
        click.echo(f"  IP: {ip}")


if __name__ == '__main__':
    cli()
