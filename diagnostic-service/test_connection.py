from kubernetes import client, config

config.load_kube_config()

v1 = client.CoreV1Api()

response = v1.list_namespaced_pod(namespace="aiops")  # You can specify the namespace you want to query
for pod in response.items:
    print(f"\nPod: {pod.metadata.name}")
    print(f"Phase: {pod.status.phase}")

    for container in pod.status.container_statuses or []:
        print(f"Container: {container.name}")
        print(f"Ready: {container.ready}")
        print(f"Restarts: {container.restart_count}")

        state = container.state

        if state.waiting:
            print(f"State: Waiting")
            print(f"Reason: {state.waiting.reason}")

        elif state.running:
            print("State: Running")

        elif state.terminated:
            print("State: Terminated")
            print(f"Reason: {state.terminated.reason}")
            print(f"Exit code: {state.terminated.exit_code}")
        last_state = container.last_state
        if last_state.terminated:
            print("Last State: Terminated")
            print(f"Reason: {last_state.terminated.reason}")
            print(f"Exit code: {last_state.terminated.exit_code}")
        
        # Simple failure detection
        if container.state.waiting:
            reason = container.state.waiting.reason

            if reason == "CrashLoopBackOff":
                print("ALERT: Container is crash-looping!")

        if container.last_state.terminated:
            last_reason = container.last_state.terminated.reason

            if last_reason == "OOMKilled":
                print("ALERT: Container was killed due to memory!")

        if container.restart_count > 0:
            print(
                f"WARNING: Container has restarted "
                f"{container.restart_count} times"
            )
logs = v1.read_namespaced_pod_log(
    name="crash-demo",
    namespace="aiops",
    container="crashing-container",
    previous=True,
    tail_lines=20
)

print(logs)