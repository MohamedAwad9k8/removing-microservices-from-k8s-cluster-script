from pathlib import Path
import os
import gitlab
import subprocess
import requests
import hvac
import yaml
from dotenv import load_dotenv

# === LOAD CONFIG FROM .env ===
load_dotenv()

#Connection Variables
GITLAB_URL = os.getenv("GITLAB_URL")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
PROJECT_PATH = os.getenv("PROJECT_PATH")
ARGOCD_URL = os.getenv("ARGOCD_URL")
VAULT_URL = os.getenv("VAULT_URL")
VAULT_TOKEN = os.getenv("VAULT_TOKEN")


#Ticket Variables
SOURCE_BRANCH = os.getenv("SOURCE_BRANCH")
TICKET_NUMBER= os.getenv("TICKET_NUMBER")

#Payload Variables
ARGOAPP_DOMAIN= os.getenv("ARGOAPP_DOMAIN")
SERVICE_NAME= os.getenv("SERVICE_NAME")
BACKEND_SERVICE= os.getenv("BACKEND_SERVICE", "0") == "1"
DB_TYPE= os.getenv("DB_TYPE")
NAMESPACE = os.getenv("K8S_NAMESPACE", "default")
CONTEXT = os.getenv("K8S_CONTEXT")
VAULT_SECRET_ENGINE_NAME = os.getenv("VAULT_SECRET_ENGINE_NAME", "configmaps")
VAULT_CONFIGMAP_BACKUP_DIR = os.getenv("VAULT_CONFIGMAP_BACKUP_DIR", "deleted_configmaps_backup")

#Values Based on Ticket Variables
commit_m = f"{TICKET_NUMBER}-remove {SERVICE_NAME}"
NEW_BRANCH = TICKET_NUMBER
COMMIT_MESSAGE = commit_m
MR_TITLE = commit_m

#Values Based on Payload Variables
FILE_PATH = f"{ARGOAPP_DOMAIN}/{SERVICE_NAME}.yaml"

# Which parts to skip, optional
skip_remove_argoApp_yaml=False
skip_remove_finalizer=False
skip_delete_argoApp=False
skip_backup_and_delete_configmap=False
skip_delete_vault_DB_connection_roles=False
skip_delete_vault_service_policies=False
skip_delete_vault_service_access_roles=False

# confirmation prompt
def user_prompt_confirmation():
    confirm = input(
        f"\nYou are about to remove a service\n"
        f"  • Branch: {SOURCE_BRANCH}\n"
        f"  • Ticket Number: {TICKET_NUMBER}\n"
        f"  • SERVICE_NAME: {SERVICE_NAME}\n"
        f"  • Service is Backend: {BACKEND_SERVICE} and DB Type is {DB_TYPE} (for backend services only)\n"
        "Please review the details above.\n"
        "Press Y to continue, or N to abort and modify your .env file: "
    ).strip().lower()
    if confirm != "y":
        print("Aborted by user. Please update your .env and re-run.")
        exit(1)
    else:
        print("Continuing with removing deprecated service…")

#################################
# Step 1 - Remove Argo App Yaml #
#################################

def remove_argoApp_yaml(skip_step=False):
    if skip_step:
        print("[Skipping] Argo App YAML removal step as requested.")
        return

    print(f"Step 1 - Removing Argo App Yaml file for '{SERVICE_NAME}'...")
    # === INIT GITLAB CONNECTION ===
    try:
        gl = gitlab.Gitlab(GITLAB_URL, private_token=GITLAB_TOKEN)
        gl.auth()  # Force authentication to validate the token
        project = gl.projects.get(PROJECT_PATH)
    except gitlab.exceptions.GitlabAuthenticationError:
        print("[ERROR] Failed to authenticate with GitLab. Please make sure your private token is valid.")
        exit(1)
    except gitlab.exceptions.GitlabGetError as e:
        print(f"[ERROR] Failed to retrieve project '{PROJECT_PATH}': {e}")
        exit(1)
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred: {e}")
        exit(1)

    # === CREATE BRANCH ===
    try:
        project.branches.create({'branch': NEW_BRANCH, 'ref': SOURCE_BRANCH})
        print(f"[OK] Branch '{NEW_BRANCH}' created, based on source-branch: {SOURCE_BRANCH}")
    except gitlab.exceptions.GitlabCreateError:
        print(f"Branch '{NEW_BRANCH}' already exists!!! Continuing...")

    # === CHECK IF FILE EXISTS AND DELETE IT IF FOUND ===
    file_extensions = [".yaml", ".yml"]
    file_to_delete = None

    for ext in file_extensions:
        try:
            path = f"{ARGOAPP_DOMAIN}/{SERVICE_NAME}{ext}"
            f = project.files.get(file_path=path, ref=NEW_BRANCH)
            file_to_delete = path
            print(f"[OK] Found file to delete: {file_to_delete}")
            break
        except gitlab.exceptions.GitlabGetError:
            continue

    if not file_to_delete:
        print(f"[ERROR] No file found with extensions .yaml or .yml under {ARGOAPP_DOMAIN}/")
        exit(1)

    #Delete the file
    project.files.delete(
        file_path=file_to_delete,
        branch=NEW_BRANCH,
        commit_message=COMMIT_MESSAGE
    )
    print(f"Deleted file: {file_to_delete}")

    # === CREATE MERGE REQUEST ===
    mr = project.mergerequests.create({
        'source_branch': NEW_BRANCH,
        'target_branch': SOURCE_BRANCH,
        'title': MR_TITLE
    })
    print(f"[OK] Merge request created: {mr.web_url}")

    # === PROMPT USER TO MERGE ===
    merge_confirm = input(
        f"\nWould you like to merge this MR now?\n"
        f"  • Title: {MR_TITLE}\n"
        f"  • URL:   {mr.web_url}\n\n"
        "Press Y to merge, or N to skip: "
    ).strip().lower()

    if merge_confirm == "y":
        try:
            mr.merge()
            print("[OK] Merge request merged successfully.")
        except gitlab.exceptions.GitlabMRClosedError:
            print("[ERROR] Merge failed: MR is already closed.")
        except Exception as e:
            print(f"[ERROR] Merge failed: {e}")
    else:
        print("Merge skipped. You can merge manually later.")

#############################
# Step 2 - Remove Finalizer #
#############################

def remove_finalizer(skip_step=False):
    if skip_step:
        print("[Skipping] Finalizer removal step as requested.")
        return

    print(f"Step 2 - Removing Finalizer '{SERVICE_NAME}'...")

    try:
        subprocess.run(
            [
                "kubectl", "--context", CONTEXT, "patch", "deployment", SERVICE_NAME,
                "-n", NAMESPACE,
                "-p", '{"metadata":{"finalizers":[]}}',
                "--type=merge"
            ],
            check=True
        )
        print(f"[OK] Finalizer removed from deployment '{SERVICE_NAME}' in namespace '{NAMESPACE}'")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to remove finalizer: {e}")


############################
# Step 3 - Delete Argo App #
############################

def delete_argoApp(skip_step=False):
    if skip_step:
        print("[Skipping] ArgoApp deletion step as requested.")
        return

    print(f"Step 3 - Deleting ArgoApp for '{SERVICE_NAME}'...")

    try:
        subprocess.run(
            ["gnome-terminal", "--", "bash", "-c", f"./delete_argocd_app.sh {ARGOCD_URL} {SERVICE_NAME}; read -p 'Press enter to close...'"],
            check=True
        )
        print("[OK] Opened a new terminal to run the script.")
    except Exception as e:
        print("[ERROR] Failed to open new terminal:", str(e))

    print ("python didn't exit and is still running..............")


#===================
# IF BackEnd Service
#===================

 # === CONNECT TO VAULT ===
def get_vault_client():
    try:
        client = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)

        if not client.is_authenticated():
            print("[ERROR] Failed to authenticate with Vault. Please make sure your token is valid.")
            exit(1)
        else:
            print("[OK] Successfully authenticated with Vault.")
            return client

    except Exception as e:
        print(f"[ERROR] Error while connecting to Vault: {e}")
        exit(1)


############################################
# Step 4 - Backup configmap then delete it #
############################################

def backup_and_delete_configmap(vault_client,skip_step=False):
    if skip_step:
        print("[Skipping] Configmap deletion step as requested.")
        return
    VAULT_SECRET_ENGINE_NAME = "configmaps"

    print(f"Step 4 - Deleting Configmap for '{SERVICE_NAME}'...")

    # === BACKUP CONFIGMAP ===
    print(f"Attempting to back up Vault secret: {VAULT_SECRET_ENGINE_NAME}/{SERVICE_NAME}")

    try:
        response = vault_client.secrets.kv.v2.read_secret_version(mount_point=VAULT_SECRET_ENGINE_NAME, path=SERVICE_NAME)

        secret_data = response['data']['data']  # actual key-value data inside the secret

        # Ensure backup directory exists
        os.makedirs(VAULT_CONFIGMAP_BACKUP_DIR, exist_ok=True)

        # Build the file path: backup_dir/SERVICE_NAME.yaml
        backup_file_path = os.path.join(VAULT_CONFIGMAP_BACKUP_DIR, f"{SERVICE_NAME}.yaml")

        # Write the configmap to the file
        with open(backup_file_path, "w") as backup_file:
            yaml.dump(secret_data, backup_file, default_flow_style=False)
            print(f"[OK] Backup saved at: {backup_file_path}")
            
    except hvac.exceptions.InvalidPath:
        print(f"[ERROR] Secret not found at configmaps/{SERVICE_NAME}")
        exit(1)
    except Exception as e:
        print(f"[ERROR] Error while reading secret: {e}")
        exit(1)

    # === DELETE SECRET ===
    try:
        print(f"Deleting secret: configmaps/{SERVICE_NAME}")
        vault_client.secrets.kv.v2.delete_metadata_and_all_versions(
            mount_point="configmaps",
            path=SERVICE_NAME
        )
        print("[OK] Secret deleted successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to delete secret: {e}")
        exit(1)

##############################################
# Step 5 - Delete Vault Connection and Roles #
##############################################


def delete_vault_DB_connection_roles(vault_client,skip_step=False):
    if skip_step:
        print("[Skipping] Vault DB Connection deletion step as requested.")
        return

    print(f"Step 5 - Deleting Vault DB Connection and Roles for '{SERVICE_NAME}'...")

    if DB_TYPE == "mysql":
        VAULT_SECRET_ENGINE_NAME = <UPDATE SUITABLE VALUE HERE>
    elif DB_TYPE == "mongodb":
        VAULT_SECRET_ENGINE_NAME = <UPDATE SUITABLE VALUE HERE>
    else:
        print(f"Exiting!!!  Unsupported DB_TYPE: {DB_TYPE}")
        return

    db_name =f"{SERVICE_NAME}-{DB_TYPE}"

    # Delete the database connection
    try:
        vault_client.secrets.database.delete_connection(
            name=db_name,
            mount_point=VAULT_SECRET_ENGINE_NAME
        )
        print(f"[OK] Deleted DB connection: {db_name}")
    except Exception as e:
        print(f"[ERROR] Failed to delete DB connection: {db_name}. Reason: {e}")

    # Delete the database connection Roles
    try:
        # List all roles under the secret engine
        roles_response = vault_client.secrets.database.list_roles(
            mount_point=VAULT_SECRET_ENGINE_NAME
        )
        role_names = roles_response.get("data", [])
        role_names = role_names["keys"]
    except Exception as e:
        print(f"[ERROR] Failed to list DB roles. Reason: {e}")
        role_names = []

    # Filter roles that end with {service_name}-mongodb
    matching_roles = [
        role for role in role_names
        if role.endswith(db_name)
    ]

    print(f"Matching Roles: {matching_roles}")
    # Delete matching roles
    for role in matching_roles:
        try:
            vault_client.secrets.database.delete_role(
                name=role,
                mount_point=VAULT_SECRET_ENGINE_NAME
            )
            print(f"[OK] Deleted DB role: {role}")
        except Exception as e:
            print(f"[ERROR] Failed to delete DB role: {role}. Reason: {e}")


##############################################
# Step 6 - Delete Vault policies for service #
##############################################

def delete_vault_service_policies(vault_client,skip_step=False):
    if skip_step:
        print("[Skipping] Service Vault Policies deletion step as requested.")
        return

    print(f"Step 6 - Deleting Vault Policies for '{SERVICE_NAME}'...")

    try:
        # List all policies
        policies = vault_client.sys.list_policies()
        policies = policies["keys"]
    except Exception as e:
        print(f"[ERROR] Failed to list Vault policies. Reason: {e}")
        return

    # Filter policies that end with the SERVICE_NAME
    matching_policies = [
        policy for policy in policies if policy.endswith(SERVICE_NAME)
    ]

    print(f"Matching policies for deletion: {matching_policies}")

    # Delete matching policies
    for policy in matching_policies:
        try:
            vault_client.sys.delete_policy(name=policy)
            print(f"[OK] Deleted policy: {policy}")
        except Exception as e:
            print(f"[ERROR] Failed to delete policy: {policy}. Reason: {e}")

##################################################
# Step 7 - Delete Vault Access Roles for service #
##################################################

def delete_vault_service_access_roles(vault_client,skip_step=False):
    if skip_step:
        print("[Skipping] Service Vault Access Roles deletion step as requested.")
        return

    print(f"Step 3 - Deleting Vault Access Roles for '{SERVICE_NAME}'...")

    # Set your auth mount point (this might be 'eks' or another, depending on your Vault config)
    mount_point = <ENTER VALUE HERE>
        
    # Step 2: List roles under the specified mount
    try:
        url = f"/v1/auth/{mount_point}/role"
        response = vault_client.adapter.request("LIST", url)  # Already returns a dict
        role_names = response.get("data", {}).get("keys", [])
    except Exception as e:
        print(f"[ERROR] Failed to list roles in '{mount_point}'. Reason: {e}")
        role_names = []

    # Step 3: Filter and delete roles that match SERVICE_NAME
    matching_roles = [r for r in role_names if r.endswith(SERVICE_NAME)]
    print(f"Matching roles for deletion: {matching_roles}")

    for role in matching_roles:
        try:
            del_url = f"/v1/auth/{mount_point}/role/{role}"
            vault_client.adapter.request("DELETE", del_url)
            print(f"[OK] Deleted role: {role}")
        except Exception as e:
            print(f"[ERROR]  Failed to delete role '{role}'. Reason: {e}")

#################
# Main Function #
#################

def main():
    user_prompt_confirmation()
    remove_argoApp_yaml(skip_remove_argoApp_yaml)
    remove_finalizer(skip_remove_finalizer)
    delete_argoApp(skip_delete_argoApp)
    if not BACKEND_SERVICE:
        print(f"This is a front-end service the script has finished deleteing {SERVICE_NAME}, make sure to remove non-utilized API Routes and DNS Records related to this service.")
        exit(0)
    else:
        vault_client = get_vault_client()
        backup_and_delete_configmap(vault_client, skip_backup_and_delete_configmap)
        delete_vault_DB_connection_roles(vault_client, skip_delete_vault_DB_connection_roles)
        delete_vault_service_policies(vault_client, skip_delete_vault_service_policies)
        delete_vault_service_access_roles(vault_client, skip_delete_vault_service_access_roles)

if __name__ == "__main__":
    main()