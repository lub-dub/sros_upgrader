from netmiko import ConnectHandler, file_transfer, progress_bar
import argparse
import re
from getpass import getpass
import tempfile
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser(
    prog="copy_upgrade", description="preps nokia for upgrade"
)

parser.add_argument("--get_info", action="store_true")
parser.add_argument("--dryrun", action="store_true")
parser.add_argument("--delete", action="store_true")
parser.add_argument("--copy")
parser.add_argument("--efi", action="store_true")
parser.add_argument("--password", action="store_true")
parser.add_argument("username")
parser.add_argument("host")
parser.set_defaults(get_info=True)

version_re = re.compile(r"([0-9]{2}\.(?:3|7|10)\.R[1-9])")
files_re = re.compile(r"([0-9]+) File\(s\)")


def return_match(match):
    return match.group()


def get_count(match):
    groups = match.groups()
    if len(groups) != 0:
        return int(groups[0])
    return None


def check_dir(con, path):
    ret = net_connect.send_command(f"file change-directory {path}")
    if ret:
        print(f"{path} {ret}")
        return False
    return True


def count_dir(con, path):
    if check_dir(con, path):
        file_listing = net_connect.send_command("file list")
        matches = files_re.search(file_listing)
        iter_count = get_count(matches)
        return iter_count
    return None


def main():
    arguments = parser.parse_args()
    connection_details = {
        "device_type": "nokia_sros",
        "host": arguments.host,
        "username": arguments.username,
        "ssh_config_file": "~/.ssh/config",
    }

    if arguments.password:
        connection_details["password"] = getpass()
    else:
        connection_details["use_keys"] = (True,)
        connection_details["key_file"] = "~/.ssh/id_rsa"

    net_connect = ConnectHandler(**connection_details)

    print(net_connect.find_prompt())
    version = net_connect.send_command("state system version version-number")
    boot_location = net_connect.send_command("state system bootup image-source")
    primary_image = net_connect.send_command(
        "admin show configuration bof flat bof image primary-location"
    )
    secondary_image = net_connect.send_command(
        "admin show configuration bof flat bof image secondary-location"
    )
    tertiary_image = net_connect.send_command(
        "admin show configuration bof flat bof image tertiary-location"
    )
    if (
        return_match(version_re.search(version))
        != return_match(version_re.search(primary_image))
        and boot_location != "image-source primary"
    ):
        print("System not booted from primary image and/or version does not match")
        quit(1)

    if arguments.delete and secondary_image:
        print("lets clean up some space")
        file_path = secondary_image.strip().split(" ")[1].strip('"')
        iter_count = count_dir(net_connect, file_path)
        if iter_count:
            arg = input(
                net_connect.send_command(
                    f"file remove {file_path}\*", expect_string="Delete"
                )
            )
            for i in range(iter_count - 1):
                arg = input(net_connect.send_command(arg, expect_string="Delete"))
            net_connect.write_channel(f"{arg}\n")
            print(net_connect.read_until_prompt())
        iter_count = count_dir(net_connect, file_path)
        if iter_count != None or iter_count == 0:
            print("removing dir....")
            net_connect.send_command("file change-directory cf3:")
            arg = input(
                net_connect.send_command(
                    f"file remove-directory {file_path}", expect_string="Are you sure"
                )
            )
            net_connect.write_channel(f"{arg}\n")
            print(net_connect.read_until_prompt())
        elif iter_count and iter_count != 0:
            print("Directory not empty, aborting...")
            net_connect.disconnect()
            quit(1)

    if arguments.copy:
        print(arguments.copy)
        if not zipfile.is_zipfile(arguments.copy):
            print("file not zip")
            quit(1)
        with tempfile.TemporaryDirectory() as tmpdirname:
            archive = zipfile.ZipFile(arguments.copy)
            print(archive.namelist())
            print(tmpdirname)
            archive.extractall(path=tmpdirname)
            tmp_dir = Path(f"{tmpdirname}/cflash")
            efi = tmp_dir / "EFI"
            print(efi)
            if arguments.efi:
                for item in efi.rglob("*"):
                    if not item.is_dir():
                        dst_path = str(item).split("cflash")[1]

                        print(
                            file_transfer(
                                net_connect,
                                source_file=item,
                                dest_file=dst_path,
                                overwrite_file=True,
                                file_system="cf3:",
                            )
                        )

            print(
                file_transfer(
                    net_connect,
                    source_file=tmp_dir / "boot.ldr",
                    dest_file=f"boot.ldr",
                    overwrite_file=True,
                    file_system="cf3:",
                )
            )

            for item in tmp_dir.glob("TiMOS-*"):
                if not check_dir(net_connect, f"cf3:\{item.name}"):
                    print(
                        net_connect.send_command(
                            f"file make-directory cf3:\{item.name}"
                        )
                    )
                for child in item.iterdir():
                    print(f"{item.name}/{child.name}")
                    print(
                        file_transfer(
                            net_connect,
                            source_file=child,
                            dest_file=f"{item.name}/{child.name}",
                            file_system="cf3:",
                        )
                    )

    net_connect.disconnect()

if __name__ == "__main__":
    main()
