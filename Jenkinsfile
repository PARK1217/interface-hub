pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20', daysToKeepStr: '30'))
        disableConcurrentBuilds()
        timestamps()
    }

    environment {
        // OCI VM(susan-vm-01)으로 SSH 배포. Jenkins 가 docker 컨테이너로 동작하므로
        // 호스트에서 직접 docker compose 하지 않고 SSH 로 OCI 에 접속해 배포한다.
        DEPLOY_HOST = 'ubuntu@168.107.40.16'
        APP_DIR     = '/home/ubuntu/interface-hub'
        APP_URL     = 'https://interface-hub.chobihome.site'
    }

    triggers {
        githubPush()
    }

    stages {
        stage('Deploy to OCI') {
            steps {
                echo "🐳 OCI 배포: git pull + 컨테이너 재빌드..."
                withCredentials([sshUserPrivateKey(credentialsId: 'kitten-deploy-key', keyFileVariable: 'SSH_KEY')]) {
                    sh """
                        ssh -i "\$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ${DEPLOY_HOST} ' \
                            set -e && \
                            cd ${APP_DIR} && \
                            git pull --ff-only && \
                            test -f backend/.env && \
                            docker compose up -d --build backend frontend && \
                            docker compose ps \
                        '
                    """
                }
            }
        }

        stage('Health Check') {
            steps {
                echo "💚 서비스 응답 확인..."
                withCredentials([sshUserPrivateKey(credentialsId: 'kitten-deploy-key', keyFileVariable: 'SSH_KEY')]) {
                    sh """
                        ssh -i "\$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ${DEPLOY_HOST} ' \
                            ok=0; \
                            for i in 1 2 3 4 5 6; do \
                                if curl -fsS http://localhost:8081/docs >/dev/null 2>&1; then echo "✓ Backend OK"; ok=1; break; fi; \
                                echo "  대기 중 (\$i/6)..."; sleep 5; \
                            done; \
                            [ \$ok -eq 1 ] || { echo "❌ 응답 없음"; exit 1; }; \
                            curl -fsS http://localhost:8081/ >/dev/null 2>&1 && echo "✓ Frontend OK" \
                        '
                    """
                }
            }
        }
    }

    post {
        success { echo "🎉 배포 성공! ${APP_URL}" }
        failure { echo "❌ 배포 실패. Console Output 확인 필요." }
    }
}
