import requests
import json
import subprocess

PROMETHEUS_URL = "http://kube-prometheus-stack-prometheus.monitoring.svc:9090/api/v1/query"
ISTIO_INGRESS_NAMESPACE = "intern"
VIRTUAL_SERVICE_NAME = "sd-api-virtual"

# 사전에 정의된 GPU 모델 성능
GPU_PERFORMANCE = {
    "NVIDIA GeForce RTX 3070": 1.0,
    "NVIDIA GeForce RTX 4070": 1.4,
}


def get_gpu_metrics():
    # GPU 메트릭 수집 쿼리
    gpu_usage_query = "DCGM_FI_DEV_GPU_UTIL{}"
    mem_usage_query = "DCGM_FI_DEV_MEM_COPY_UTIL{}"
    power_usage_query = "DCGM_FI_DEV_POWER_USAGE{}"

    # Prometheus에서 데이터 가져오기
    gpu_usage = requests.get(f"{PROMETHEUS_URL}?query={gpu_usage_query}").json()
    mem_usage = requests.get(f"{PROMETHEUS_URL}?query={mem_usage_query}").json()
    power_usage = requests.get(f"{PROMETHEUS_URL}?query={power_usage_query}").json()

    return gpu_usage, mem_usage, power_usage


def calculate_node_score(gpu_perf, gpu_usage, mem_usage, power_usage):
    # 상태 점수 계산
    performance_weight = 100
    usage_weight = 0.3
    mem_weight = 0.2
    power_weight = 0.1

    score = (
            performance_weight * gpu_perf -
            usage_weight * gpu_usage -
            mem_weight * mem_usage -
            power_weight * power_usage
    )
    return max(score, 0)


def update_virtual_service(weights):
    # VirtualService 가중치 업데이트를 위한 JSON Patch 생성
    routes = []
    total_score = sum(weights.values())

    for idx, (subset_name, score) in enumerate(weights.items()):
        weight = int((score / total_score) * 100)
        routes.append({
            "op": "replace",
            "path": f"/spec/http/0/route/{idx}/weight",
            "value": weight
        })

    patch_payload = json.dumps(routes)

    try:
        subprocess.run(
            [
                "kubectl", "patch", "virtualservice", VIRTUAL_SERVICE_NAME,
                "-n", ISTIO_INGRESS_NAMESPACE, "--type=json", "-p", patch_payload
            ],
            check=True
        )
        print("VirtualService updated successfully!")
    except subprocess.CalledProcessError as e:
        print("Error updating VirtualService:")
        print(e.stderr)


def main():
    gpu_usage, mem_usage, power_usage = get_gpu_metrics()

    weights = {}

    # Prometheus에서 가져온 GPU 메트릭을 기반으로 가중치 계산
    for gpu in gpu_usage["data"]["result"]:
        pod_name = gpu["metric"].get("pod")
        if not pod_name or not pod_name.startswith("stable-diffusion-api"):
            continue
        subset_name = pod_name  # subset 이름은 Pod 이름과 동일하게 매핑

        # GPU 성능과 사용량 데이터 가져오기
        gpu_perf = GPU_PERFORMANCE.get(gpu["metric"].get("modelName", "NVIDIA GeForce RTX 3070"), 1.0)
        gpu_used = float(gpu["value"][1])

        # GPU 메모리 및 전력 소모 정보 가져오기
        mem_used = next(
            (float(m["value"][1]) for m in mem_usage["data"]["result"] if m["metric"].get("pod") == pod_name),
            0
        )
        power_used = next(
            (float(p["value"][1]) for p in power_usage["data"]["result"] if p["metric"].get("pod") == pod_name),
            0
        )

        # 점수 계산
        score = calculate_node_score(gpu_perf, gpu_used, mem_used, power_used)
        weights[subset_name] = score

    # VirtualService 업데이트
    update_virtual_service(weights)


if __name__ == "__main__":
    main()
