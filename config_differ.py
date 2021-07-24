#!/usr/bin/env python3
"""
# config_differ
![Screenshot](.main.png)


The script generates a report about configuration changes on network devices and sends it to the mail receivers.


It runs every day via CRON:
```
PATH=/usr/local/bin/:/usr/bin:/usr/sbin

# Config differ.
00 07 * * * /usr/bin/python3.5 config_differ.py >/dev/null 2>&1
```

Requirements:
* python3.5 >=
* python3.5 -m pip install -r requirements.txt

`templates/html_template.jinja` - you can change html template.

`settings.yml.example` - fill it and rename into `settings.yml`.

"""

import argparse
import datetime
import calendar
import subprocess
import os
import logging
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import yaml
from jinja2 import Environment, FileSystemLoader


def get_date():
    """ """
    today = datetime.datetime.now()
    weekday = calendar.day_name[today.weekday()]
    date = datetime.datetime.strftime(today, '%Y-%m-%d %H:%M:%S')
    return date, weekday


def send_email(SETTINGS, subj, text, html):
    """ """
    message = MIMEMultipart("alternative")
    recievers = ', '.join(SETTINGS["EMAIL_RECIEVERS"])
    message['Subject'] = subj
    message['From'] = SETTINGS["SMTP_SENDER"]
    message['To'] = recievers
    part1 = MIMEText(text, "plain")
    part2 = MIMEText(html, "html")
    message.attach(part1)
    message.attach(part2)
    server = smtplib.SMTP_SSL(SETTINGS["SMTP_SERVER"], SETTINGS["SMTP_PORT"])
    server.login(SETTINGS["SMTP_SENDER"], SETTINGS["SMTP_PASSWORD"])
    server.sendmail(SETTINGS["SMTP_SENDER"],
                    SETTINGS["EMAIL_RECIEVERS"],
                    message.as_string())
    server.quit()


def generate_html(result, path):
    """Render result data with Jinja2 to html."""
    env = Environment(
        loader=FileSystemLoader(os.path.join(path, 'templates')),
        trim_blocks=True,
        lstrip_blocks=True)
    template = env.get_template('html_template.jinja')
    html_report = template.render(result)

    # Save result in a file
    report_file_path_html = os.path.join(path, 'results', 'config_diff.html')
    with open(report_file_path_html, 'w') as file:
        file.write(html_report)
    logging.info('Diff report saved in HTML: {}'.format(report_file_path_html))
    return html_report


def find_hostname(SETTINGS, file_name):
    """ """
    hostname = 'HOSTNAME-IS-UNKNOW'

    command = ('git --no-pager diff HEAD^ HEAD -U$(wc -l '
            '{file}) {file}').format(file=file_name)
    output = subprocess.run(command, shell=True,
                            stdout=subprocess.PIPE)
    if output.returncode == 0:
        for line in output.stdout.decode('utf-8').split('\n'):
            for regex in SETTINGS["HOSTNAME_PREFIXES"]:
                match = re.search(r'{}'.format(regex), line)
                if match:
                    return match.groups()[0].strip('";')
    return hostname


def parse_config(SETTINGS, config):
    """ """
    def has_useless_lines(SETTINGS, line):
        for regex in SETTINGS["TRASH_LINES"]:
            match = re.search(r"{}".format(regex), line)
            if match:
                return True
        return False


    def vlan_str_range_to_list(vlans):
        """ Convert VLAN VIDs ranges to a list.

        Parametres
        ----------
        str:
            A string of ranges '1-3,5,7-8'

        Returns
        -------
        str:
            A list of ints [1,2,3,5,7,8]
        """
        ls = []

        for v in vlans.strip().split(','):
            if '-' in v:
                # Convert str to int
                v1, v2 = list(map(int, v.split('-')))
                while True:
                    if v1 <= v2:
                        ls.append(v1)
                        v1 += 1
                    else:
                        break
            else:
                ls.append(int(v))
        return ls


    def vlan_list_to_str_range(vlans):
        """ Convert sequences of VLAN VIDs into ranges.

        Parametres
        ----------
        list:
            A list of ints [1,2,3,5,7,8]

        Returns
        -------
        str:
            A string of ranges '1-3,5,7-8'
        """
        # Sort and get unique values from a list
        vlans = list(set(vlans))
        vlans = sorted(vlans)

        # Create ranges
        st = ''
        l = len(vlans) - 1

        for i, v in enumerate(vlans):
            if i < l:
                if (v == vlans[i + 1] - 1):
                    if i > 0:
                        if st[-1:][0] != '-':
                            st += str(v) + '-'
                    else:
                        st += str(v) + '-'
                else:
                    st += str(v) + ','
            else:
                st += str(v) + ','
        return st[:-1]


    def get_context_len(line):
        """Get number of whitespaces from config line, it defines context."""
        match = re.search(r'([-+]?\s*).*', line)
        if match:
            return len(match.groups()[0])


    def split_by_parts(vl, part_size):
        """Split huge number of vlans on parts."""
        return [vl[v:v + part_size] for v in range(0, len(vl), part_size)]


    def find_vlan_changes(count):
        """For visual presentation, separate the modified VLAN lines
        from the unmodified ones.
        """
        result = []
        raw_result = []
        vlan_before = []
        vlan_after = []
        all_vlans = []
        has_changes = False
        for line in config[count:]:
            count += 1
            line = line.rstrip()
            match = re.search(r'[-+]?\s*switchport trunk allowed vlan (?:add )?((?:\d+[,-]?)+)', line)
            # Match Vlan VIDs
            if match:
                temp_vlans = vlan_str_range_to_list(match.groups()[0])
                if match.group().startswith('-'):
                    has_changes = True
                    vlan_before.extend(temp_vlans)
                    all_vlans.extend(temp_vlans)
                elif match.group().startswith('+'):
                    has_changes = True
                    vlan_after.extend(temp_vlans)
                    all_vlans.extend(temp_vlans)
                else:
                    all_vlans.extend(temp_vlans)
                    raw_result.append(line)
            else:
                # Vlan config lines not found
                count -= 1
                break

        if has_changes:
            context_len = get_context_len(config[count])

            # Vlans that not modified
            all_vlans = list(set(all_vlans))

            vlans = []
            if vlan_before and vlan_after:
                unmod_vlans = set(vlan_before).symmetric_difference(set(vlan_after))
                if unmod_vlans:
                    for vlan in unmod_vlans:
                        if vlan in all_vlans:
                            all_vlans.remove(vlan)

                vlans = vlan_list_to_str_range(all_vlans)
                for chunk in split_by_parts(vlans.split(','), 10):
                    if chunk:
                        if result:
                            line = (' '*context_len) + 'switchport trunk allowed vlan add '
                        else:
                            line = (' '*context_len) + 'switchport trunk allowed vlan '
                        result.append(line + ','.join(chunk))

            # Deleted Vlans
            vlans = []
            for vlan in vlan_before:
                if vlan not in vlan_after:
                    vlans.append(vlan)
            if vlans:
                vlans = vlan_list_to_str_range(vlans)
                for chunk in split_by_parts(vlans.split(','), 10):
                    if chunk:
                        if result:
                            line = '-' + (' '*(context_len-1)) + 'switchport trunk allowed vlan add '
                        else:
                            line = '-' + (' '*(context_len-1)) + 'switchport trunk allowed vlan '
                        result.append(line + ','.join(chunk))

            # Added Vlans
            vlans = []
            for vlan in vlan_after:
                if vlan not in vlan_before:
                    vlans.append(vlan)
            if vlans:
                vlans = vlan_list_to_str_range(vlans)
                for chunk in split_by_parts(vlans.split(','), 10):
                    if chunk:
                        if result:
                            line = '+' + (' '*(context_len-1)) + 'switchport trunk allowed vlan add '
                        else:
                            line = '+' + (' '*(context_len-1)) + 'switchport trunk allowed vlan '
                        result.append(line + ','.join(chunk))

            return result, count
        else:
            return raw_result, count


    # Pass diff headers
    config = config.strip().split('\n')
    config = config[5:]

    result = []
    jump = 0
    has_diffs = False

    for count, line in enumerate(config):
        line = line.replace('\r', '').replace('\n', '')
        if count < jump:
            # Line already saved, pass
            continue
        if has_useless_lines(SETTINGS, line):
            # Line has useless dynamic data, pass
            continue
        if '@@' in line:
            # Delete diff chunks info from line
            line = re.split(r'@@ .*@@', line)
            if len(line) == 1:
                line = ''.join(line)
            else:
                line = ''.join(line[1:])

        if 'switchport trunk allowed vlan' in line:
            lines, jump = find_vlan_changes(count)
            result.extend(lines)
        else:
            if line.startswith(('-', '+')):
                has_diffs = True
            result.append(line)

    if has_diffs:
        return result
    else:
        return None


def main():
    # Argument parser
    parser = argparse.ArgumentParser(description='Diff-report generator')
    parser.add_argument(
        '--dry-run',
        dest='dry_run',
        action='store_true',
        help="Generate report, but don't send to receivers"
        )
    args = parser.parse_args()

    # Logging config
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | PID: %(process)d | %(levelname)s~: %(message)s',
        handlers=[logging.StreamHandler()]
    )

    # Get the absolute path to the script being run
    path = os.path.dirname(os.path.abspath(__file__))
    # Load settings from yaml file
    with open(os.path.join(path, 'settings.yml')) as file:
        SETTINGS = yaml.safe_load(file)
    if not os.path.exists(os.path.join(path, SETTINGS["RESULTS_DIR"])):
        os.mkdir(os.path.join(path, SETTINGS["RESULTS_DIR"]))

    # Change the current working Directory
    os.chdir(SETTINGS["BACKUP_DIR"])

    # Get all configs file names
    config_files = []
    for f in os.listdir('.'):
        if os.path.isfile(f):
            config_files.append(f)

    date, weekday = get_date()
    # Get diffs
    result = {
        "devices": [],
        "date": date,
        "weekday": weekday
    }

    for file_name in config_files:
        # Get diff between current and previous config
        command = SETTINGS["COMMAND_GIT_DIFF"].format(file=file_name)
        output = subprocess.run(command, shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        if output.returncode != 0:
            logging.warning("Command failed [{}]".format(output.stderr))
        elif not output.stdout.decode('utf-8'):
            logging.warning("Output is empty [{}]".format(command))
        else:
            logging.info("Working on [{}]".format(command))
            hostname = find_hostname(SETTINGS, file_name)
            config = parse_config(SETTINGS, output.stdout.decode('utf-8'))
            if config:
                logging.info("Diffs found [{}]".format(command))
                result["devices"].append({
                                        "hostname": hostname,
                                        "config": config
                                        })
            else:
                logging.warning("Diffs has useless dynamic data,"
                                " pass [{}]".format(command))

    # Render result data with Jinja2
    html_report = generate_html(result, path)

    # Generate TXT from result
    text_report = ""

    if args.dry_run:
        # Print to stdout
        for host in result["devices"]:
            print("Hostname: {}".format(host['hostname']))
            input("more")
            print('\n'.join(host['config']))
            input("more")
    else:
        # Send data via whatever you want.
        send_email(SETTINGS,
                "Cbackup Report - Daily configuration diffs "
                " {}".format(date.split()[0]),
                text_report, html_report)
        logging.info("Diff sended via e-mail.")


if __name__=="__main__":
    main()
