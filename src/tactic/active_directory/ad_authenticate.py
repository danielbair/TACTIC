###########################################################
#
# Copyright (c) 2005, Southpaw Technology
#                     All Rights Reserved
#
# PROPRIETARY INFORMATION.  This software is proprietary to
# Southpaw Technology, and is not to be reproduced, transmitted,
# or disclosed in any way without written permission.
#
#
#

__all__ = ['ADAuthenticate', 'ADException']

import types, os

from pyasm.common import SecurityException, Config
from pyasm.security import Authenticate
from pyasm.search import Search

from pyasm.common import Environment

try:
    #from ad import ADConnect, ADLookup, ADException
    from ad_connect import ADConnect
    from ad_lookup import ADLookup
except ImportError:
    print "WARNING: cannot import Active Directory classes"
    pass

class ADException(Exception):
    pass

INSTALL_DIR = Environment.get_install_dir()
BASE_DIR = "%s/src/tactic/active_directory" % INSTALL_DIR

class ADAuthenticate(Authenticate):
    '''Test authenticate mechanism which caches user info'''

    def __init__(my):
        my.ad_exists = True
        if os.name != 'nt':
            my.ad_exists = False

        my.groups = set()

        my.data = {}
        my.tactic_groups = []


    def get_mode(my):
        return 'cache'


    def verify(my, login_name, password):
            
        if login_name.find("\\") != -1:
            domain, login_name = login_name.split("\\")
        else:
            domain = None

        # confirm that there is a domain present if required
        require_domain = Config.get_value("active_directory", "require_domain")
        if require_domain == "true" and not domain:
            raise SecurityException("Domain Selection Required")



        # skip authentication if ad does not exist
        if not my.ad_exists:
            print "WARNING: Active directory does not exist ... skipping verify"
            return True

        ad_connect = ADConnect()
        if domain:
            ad_connect.set_domain(domain)
        ad_connect.set_user(login_name)
        ad_connect.set_password(password)
        is_logged_in = ad_connect.logon()

        # preload data for further use later
        if is_logged_in:
            my.load_user_data(login_name)

        return is_logged_in


    def get_user_mapping(my):
        '''returns a dictionary of the mappings between AD attributes to
        login table attributes'''
        attrs_map = {
            'dn':               'dn',
            'displayName':      'display_name',
            'name':             'name',
            'mail':             'email',
            'telephoneNumber':  'phone_number',
            'department':       'department',
            'employeeID':       'employee_id',
            'sAMAccountName':   'sAMAccountName',
            # some extras
            'l':                'location',
            'title':            'title',
            'tacticLicenseType': 'license_type'
        }
        return attrs_map


    def get_group_mapping(my):
        group_attrs_map = {
            'dn':               'dn',
            'name':             'name',
        }
        return group_attrs_map


    def get_tactic_license_type(my):
        '''determines from AD what license a particular user is entitled
        to'''
        key = 'tacticLicenseType'
        licence_type = my.data.get(key)
        if not license_type:
            return False
        else:
            return True


    def get_default_license_type(my):
        license_type = Config.get_value("active_directory", "default_license_type")
        if not license_type:
            license_type = 'user'
        return license_type


    def get_default_groups(my):
        groups = Config.get_value("active_directory", "default_groups")
        if not groups:
            groups = []
        else:
            groups = groups.split("|")

        return groups



    def get_user_data(my, key=None):
        if key:
            return my.data.get(key)
        else:
            return my.data


    def load_user_data(my, login_name):
        '''get user data from active directory'''

        # get all of the tactic groups
        my.tactic_groups = Search.eval("@SOBJECT(sthpw/login_group)")

        # get the mappings
        attrs_map = my.get_user_mapping()
        group_attrs_map = my.get_group_mapping()

        if my.ad_exists:
            my.data = my.get_info_from_ad(login_name, attrs_map)
        else:
            #group_path = "%s/AD_group_export.ldif" % BASE_DIR
            #my.group_data = my.get_info_from_file(login_name, group_attrs_map, group_path)
            path = "%s/AD_user_export.ldif" % BASE_DIR
            my.data = my.get_info_from_file(login_name, attrs_map, path)

        return my.data



    def add_user_info(my, login, password):
        ''' sets all the information about the user'''

        login_name = login.get_value("login")
        data = my.data


        # split up display name into first and last name
        display_name = data.get('display_name')
	try:
	    first_name, last_name = display_name.split(' ', 1)
            first_name = first_name.replace(",","")
            last_name = last_name.replace(",", "")
        except:
            first_name = display_name
	    last_name = ''

        # alter so that it works for now
        data = {
            'first_name': first_name,
            'last_name': last_name,
            'email': data.get('email'),
            'phone_number': data.get('phone_number'),
            'license_type': data.get('license_type'),
            'display_name': data.get('display_name'),
            'department':   data.get('department')
        }

        from pyasm.search import Search
        columns = Search("sthpw/login").get_columns()

        for name, value in data.items():
            if value == None:
                continue
	    if value == 'None':
	        value = ''

            # only add values that are actually in the login object
            if name not in columns:
                print "WARNING: skipping [%s].  Does not exist in login" % name
                continue

            login.set_value(name, value)


        # add all of the groups
        my.add_group_info(login)



    def get_info_from_ad(my, login_name, attrs_map):

        data = {}
        if login_name == 'admin':
	    return data

        if login_name.find("\\") != -1:
            domain, login_name = login_name.split("\\")
        else:
            domain = None

        python = Config.get_value('services', 'python')
	if not python:
	    python = 'python'

        try:
            # get the info from a separate process
            from subprocess import Popen, PIPE
            if domain:
                cmd = [python, "%s/ad_get_user_info.py" % BASE_DIR, '-d', domain, "-u", login_name]
            else:
                cmd = [python, "%s/ad_get_user_info.py" % BASE_DIR, "-u", login_name,]

            output = Popen( cmd, stdout=PIPE).communicate()[0]
            import StringIO
            output = StringIO.StringIO(output)
            data = my.get_info_from_file(login_name, attrs_map, output)

            # get the license type from active directory
            license_type = data.get('tacticLicenseType')
            if not license_type:

                # TEST!!!! for MMS
                # FIXME: this logic is questionable.
                # if the user has no defined groups in Active Directory, then
                # it should use the default license type.
                if not my.groups:
                    license_type = my.get_default_license_type()
                    data['license_type'] = license_type
                else:
                    data['license_type'] = "user"

        except ADException:
            raise SecurityException("Could not log get info from Active Directory for login [%s]" % login_name)
        return data 





    def get_info_from_file(my, login_name, attrs_map, path):
        '''for testing purposes'''

        data = {}

        if type(path) in types.StringTypes:
            f = open(path, 'r')
        else:
            f = path


        lines = []
        error_flag = False
        error = ''

        for line in f:
            if line.startswith("ERROR") or line.startswith("WARNING"):
                error_flag = True
                error = line
                print line
                continue

            if error_flag:
                print line
                continue


            if line.strip() == '':
                continue

            if line.startswith(" "):
                line = line.strip()
                lines[-1].append(line)
            else:
                line = line.strip()
                lines.append([line])

        if error_flag:
            raise SecurityException(error)

                

        for line in lines:
            line = "".join(line)
            #print "line: ", line
            name, value = line.split(": ", 1)

            if name == 'memberOf':
                my.handle_group(value)
                continue

            attr_name = attrs_map.get(name)
            #print name
            if not attr_name:
                continue
            data[attr_name] = value

        # get the license type from active directory
        license_type = data.get('license_type')
        if not license_type:

            # TEST!!!! for MMS
            # FIXME: this logic is questionable.
            # if the user has no defined groups in Active Directory, then
            # it should use the default license type.
            if not my.groups:
                license_type = my.get_default_license_type()
                data['license_type'] = license_type
            else:
                data['license_type'] = "user"

        return data




    def handle_group(my, value):

        # some values have commas in them.
        value = value.replace("\\,", "|||")
        parts = value.split(",")

        # only deal with part 1 for now
        part = parts[0]
        name, value = part.split("=")
        ad_group_name = value.replace("|||", ",")

        # provide the column which stores the ad group.
        columns = Search("sthpw/login_group").get_columns()
        group_dict = {}
        if "ad_login_group" in columns:
            mapping_col = "ad_login_group"
            for x in my.tactic_groups:
                value = x.get_value(mapping_col)
                if value:
                    group_dict[value] = x

        mapping_col = "login_group"
        for x in my.tactic_groups:
            value = x.get_value(mapping_col)
            if value:
                group_dict[value] = x


        # make sure the groups from AD are in the defined tactic groups
        if ad_group_name in group_dict:
            my.groups.add(group_dict[ad_group_name])



    def add_group_info(my, user):
        '''add the user to a specified group'''
        if not my.groups:
            default_groups = my.get_default_groups()
            my.groups.update(default_groups)


        # add a group


        remaining = user.remove_all_groups(except_list=my.groups)
        if not remaining:
            for group in my.groups:
                print "user: ", user.get_value("login")
                print "adding: ", group
                user.add_to_group(group)




