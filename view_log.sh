sudo docker logs -f taskiq_worker 2>&1 | sed 's/^/[worker] /' &
sudo docker logs -f web_service 2>&1 | sed 's/^/[web] /' &
wait