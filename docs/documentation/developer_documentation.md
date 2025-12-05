## Connect to SSH remote container in VSCode
1. Install [VSCode](https://code.visualstudio.com/download)
2. Install the following extensions in VSCode:
    - [Remote - SSH](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh)
    - [Remote - SSH: Editing Configuration Files](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh-edit)
    - [Remote Explorer](https://marketplace.visualstudio.com/items?itemName=ms-vscode.remote-explorer)
3. Open 'Remote Explorer' on the sidebar.
4. Add new remote under SSH with your SSH command.
5. Connect to the SSH. All development and deployment should be done inside the SSH remote container.

## Development
:exclamation: Make sure all development steps are done in SSH remote container.
**Clone Sentinel repository**
```git clone https://github.com/liw3n/bitbucket-code-review-agent```
**Create environment file**
```cp .env.template .env```
**Open .env file and input the necessary information.**
- feedback_forms should be a link to a Microsoft form/Google form.
- Provide links without 'https://' (eg. 'www.github.com' instead of 'https://wwww.github.com')
**Start development and push changes to Bitbucket.**

## Deployment
:exclamation: Make sure all deployment steps are done in SSH remote container.
**First Deployment**
```make build && make deploy```
- `make build`: runs `docker build -f Dockerfile -t sentinel-server:latest .`
    - Build sentinel repository onto sentinel-server image
- `make deploy`: runs `docker stack deploy --compose-file sentinel.yaml sentinel`
    - Runs the docker services defined in [sentinel.yaml](https://github.com/liw3n/bitbucket-code-review-agent/blob/main/sentinel.yaml) file (qdrant, sentinel-server, postgres)
**Subsequent Deployments**
```make redeploy```
- `make redeploy`: runs `make build && docker stack rm sentinel && docker stack deploy --compose-file sentinel.yaml sentinel`
    - Build sentinel-server image, remove previous sentinel-related services and redeploy the required docker services

## Accessing Metrics Database 
**Connect to SSH using your SSH command**
**Find docker container id running Postgres**
```docker ps | grep sentinel_db```
**Connnect to Postgres database**
```
docker exec -it <container_id> bash 
psql -U postgres -d sentinel_metrics 
```
**Access table values**
```SELECT * FROM review_metrics;```
**Database Tables**
- **review_metrics** -> Information on key metrics during the review bot process (duration, number of tokens, number of files processed, indexing, deadcode)
- **feedback** -> Ratings on review bot comments by reviewers
- **comments** -> Information on comments posted by Sentinel (comment_id, content, project, repo, pr_id)