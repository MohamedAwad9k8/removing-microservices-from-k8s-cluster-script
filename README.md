This python script automates the full process of deleting microservices from a kubernetes cluster.
The script is divided into multiple steps, each step is encaspulated inside a function, to allow modifications easily if needed for different setups.

Make sure to install the requirments, then update the values in .env file.

Note: The bash script is used by the python for the ArgoCD App Deletion step, as it leverages SSO Authentication through web browser easily.
