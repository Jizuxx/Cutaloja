import boto3
import time

# Configura tus credenciales de AWS y región
aws_region = "us-east-1"
rds_client = boto3.client("rds", region_name=aws_region)
ec2_client = boto3.client("ec2", region_name=aws_region)

# Configuración de parámetros
DB_INSTANCE_ID = "mi-db-p-final"
EC2_INSTANCE_NAME = "mi-ec2-tunnel"
KEY_NAME = "vockey"

def get_default_resources():
    # Obtiene recursos por defecto automáticamente
    try:
        # Obtener VPC por defecto
        vpcs = ec2_client.describe_vpcs(
            Filters=[{'Name': 'isDefault', 'Values': ['true']}]
        )
        if not vpcs['Vpcs']:
            raise Exception("No se encontró VPC por defecto")
        
        vpc_id = vpcs['Vpcs'][0]['VpcId']
        
        # Obtener Security Group por defecto
        sgs = ec2_client.describe_security_groups(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'group-name', 'Values': ['default']}
            ]
        )
        sg_id = sgs['SecurityGroups'][0]['GroupId']
        
        # Obtener Subnet por defecto
        subnets = ec2_client.describe_subnets(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'default-for-az', 'Values': ['true']}
            ]
        )
        subnet_id = subnets['Subnets'][0]['SubnetId']
        
        # Obtener AMI más reciente de Amazon Linux 2023
        images = ec2_client.describe_images(
            Owners=['amazon'],
            Filters=[
                {'Name': 'name', 'Values': ['al2023-ami-2023.*']},
                {'Name': 'architecture', 'Values': ['x86_64']},
                {'Name': 'state', 'Values': ['available']}
            ]
        )
        ami_id = sorted(images['Images'], key=lambda x: x['CreationDate'], reverse=True)[0]['ImageId']
        
        print(f"✓ Usando VPC: {vpc_id}")
        print(f"✓ Usando Security Group: {sg_id}")
        print(f"✓ Usando Subnet: {subnet_id}")
        print(f"✓ Usando AMI: {ami_id}")
        
        return sg_id, subnet_id, ami_id
        
    except Exception as e:
        print(f"Error obteniendo recursos: {e}")
        return None, None, None

def wait_for_rds_availability(db_instance_id):
    # Espera a que la instancia RDS esté disponible
    print("Esperando a que RDS esté disponible...")
    waiter = rds_client.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=db_instance_id)
    print("RDS está disponible!")

def wait_for_ec2_running(instance_id):
    # Espera a que la instancia EC2 esté ejecutándose
    print("Esperando a que EC2 esté ejecutándose...")
    waiter = ec2_client.get_waiter('instance_running')
    waiter.wait(InstanceIds=[instance_id])
    print("EC2 está ejecutándose!")

def get_rds_endpoint(db_instance_id):
    # Obtiene el endpoint de la base de datos RDS
    try:
        response = rds_client.describe_db_instances(DBInstanceIdentifier=db_instance_id)
        endpoint = response['DBInstances'][0]['Endpoint']['Address']
        port = response['DBInstances'][0]['Endpoint']['Port']
        return endpoint, port
    except Exception as e:
        print(f"Error al obtener endpoint RDS: {e}")
        return None, None

def get_ec2_info(instance_id):
    # Obtiene información de la instancia EC2
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance = response['Reservations'][0]['Instances'][0]
        public_ip = instance.get('PublicIpAddress')
        private_ip = instance.get('PrivateIpAddress')
        return public_ip, private_ip
    except Exception as e:
        print(f"Error al obtener info EC2: {e}")
        return None, None

def create_rds_instance():  
    sg_id, _, _ = get_default_resources()
    if not sg_id:
        return False
        
    try:
        # Verificar si ya existe
        try:
            rds_client.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)
            print(f"La instancia RDS {DB_INSTANCE_ID} ya existe.")
            return True
        except rds_client.exceptions.DBInstanceNotFoundFault:
            pass

        response = rds_client.create_db_instance(
            DBInstanceIdentifier=DB_INSTANCE_ID,
            DBInstanceClass="db.t3.micro",
            Engine="mariadb",
            AllocatedStorage=20,
            StorageType="gp2",
            MasterUsername="admin",
            MasterUserPassword="hola123mundo",
            VpcSecurityGroupIds=[sg_id],
            PubliclyAccessible=False,
            BackupRetentionPeriod=0,
            MultiAZ=False,
            StorageEncrypted=False,
            DeletionProtection=False
        )
        print("Instancia RDS creada:", response["DBInstance"]["DBInstanceIdentifier"])
        return True
    except Exception as e:
        print("Error al crear la instancia RDS:", e)
        return False

def configure_security_group():
    # Configura security group para desarrollo
    try:
        sg_id, _, _ = get_default_resources()
        
        # Abrir puerto 3000 para Express y puerto 3306 para MySQL
        try:
            ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 22,
                        'ToPort': 22,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 3000,
                        'ToPort': 3000,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 3306,
                        'ToPort': 3306,
                        'UserIdGroupPairs': [{'GroupId': sg_id}]
                    }
                ]
            )
            print("✓ Puertos 22, 3000 y 3306 configurados")
        except Exception as e:
            if "already exists" in str(e):
                print("✓ Puertos ya estaban configurados")
            else:
                print(f" Error configurando puertos: {e}")
                    
    except Exception as e:
        print(f"Error configurando security group: {e}")

def create_ec2_instance():
    sg_id, subnet_id, ami_id = get_default_resources()
    if not all([sg_id, subnet_id, ami_id]):
        return None
        
    try:
        # Verificar si ya existe una instancia con el mismo nombre
        response = ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Name', 'Values': [EC2_INSTANCE_NAME]},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )
        
        if response['Reservations']:
            instance_id = response['Reservations'][0]['Instances'][0]['InstanceId']
            print(f"La instancia EC2 {EC2_INSTANCE_NAME} ya existe: {instance_id}")
            return instance_id

        # Crear nueva instancia
        response = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType="t2.micro",
            KeyName=KEY_NAME,
            MinCount=1,
            MaxCount=1,
            SecurityGroupIds=[sg_id],
            SubnetId=subnet_id,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": EC2_INSTANCE_NAME}]
                }
            ]
        )
        instance_id = response["Instances"][0]["InstanceId"]
        print("Instancia EC2 creada:", instance_id)
        return instance_id
    except Exception as e:
        print("Error al crear la instancia EC2:", e)
        return None

def show_connection_info():
    # Muestra información para conectarse
    print("\n" + "="*50)
    print("INFORMACIÓN DE CONEXIÓN")
    print("="*50)
    
    # Obtener información de RDS
    rds_endpoint, rds_port = get_rds_endpoint(DB_INSTANCE_ID)
    if rds_endpoint:
        print(f"RDS Endpoint: {rds_endpoint}:{rds_port}")
    
    # Obtener información de EC2
    response = ec2_client.describe_instances(
        Filters=[
            {'Name': 'tag:Name', 'Values': [EC2_INSTANCE_NAME]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ]
    )
    
    if response['Reservations']:
        instance_id = response['Reservations'][0]['Instances'][0]['InstanceId']
        public_ip, private_ip = get_ec2_info(instance_id)
        
        print(f"EC2 Instance ID: {instance_id}")
        print(f"EC2 Public IP: {public_ip}")
        print(f"EC2 Private IP: {private_ip}")
        
        print("\n" + "="*50)
        print("ACCESO POR SSH")
        print("="*50)
        print("1. Conectar por SSH a EC2:")
        print(f"   ssh -i labsuser.pem ec2-user@{public_ip}")
        
        print("\n2. Datos de conexión a MariaDB:")
        if rds_endpoint:
            print(f"   Host: {rds_endpoint}")
            print(f"   Puerto: {rds_port}")
            print("   Usuario: admin")
            print("   Password: hola123mundo")
        
        print("\n3. Una vez dentro del EC2, puedes instalar:")
        print("   - Node.js y npm")
        print("   - Git para clonar repositorios")
        print("   - MySQL client para conectar a RDS")
        print("   - Cualquier otra herramienta que necesites")
        
        print(f"\n4. Tu aplicación Express estará en:")
        print(f"   http://{public_ip}:3000")

def main():
    print("Iniciando creación de infraestructura AWS...")
    
    # Configurar puerto para Express
    configure_security_group()
    
    # Crear RDS
    if create_rds_instance():
        print("✓ RDS creado/verificado")
        
        # Crear EC2
        instance_id = create_ec2_instance()
        if instance_id:
            print("✓ EC2 creado/verificado")
            
            # Esperar a que estén listos
            wait_for_ec2_running(instance_id)
            wait_for_rds_availability(DB_INSTANCE_ID)
            
            # Mostrar información de conexión
            show_connection_info()
        else:
            print("✗ Error al crear EC2")
    else:
        print("✗ Error al crear RDS")

if __name__ == "__main__":
    main()