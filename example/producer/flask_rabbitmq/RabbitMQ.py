# encoding:utf-8
from flask_rabbitmq.util._logger import logger
from . import ExchangeType
import uuid
import time
import threading
import json
import pika

class RabbitMQ(object):

    def __init__(self, app=None, queue=None):
        self.app = app
        self.queue = queue
        self.config = self.app.config
        if not (self.config.get('RPC_USER_NAME') and self.config.get('RPC_PASSWORD') and self.config.get('RPC_HOST')):
            logger.error('没有配置rpc服务器的用户名和密码')
            raise Exception('没有配置rpc服务器的用户名和密码')
        self.credentials = pika.PlainCredentials(
            self.config['RPC_USER_NAME'],
            self.config['RPC_PASSWORD']
        )
        self._connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                self.config['RPC_HOST'],
                credentials=self.credentials
            ))
        self._channel = self._connection.channel()
        self._rpc_class_list = []
        self.data = {}

    def bind_topic_exchange(self, exchange_name, routing_key, queue_name):
        """
        绑定主题交换机和队列
        :param exchange_name: 需要绑定的交换机名
        :param routing_key:
        :param queue_name: 需要绑定的交换机队列名
        :return:
        """
        self._channel.queue_declare(
            queue=queue_name,
            auto_delete=True,
            durable=True,
        )
        self._channel.exchange_declare(
            exchange=exchange_name,
            exchange_type='topic',
            auto_delete=True,
        )
        self._channel.queue_bind(
            exchange=exchange_name,
            queue=queue_name,
            routing_key=routing_key
        )

    def declare_queue(self, queue_name, passive=False, durable=False,
                      exclusive=False, auto_delete=False, arguments=None):
        """
        声明一个队列
        :param queue_name: 队列名
        :param passive:
        :param durable:
        :param exclusive:
        :param auto_delete:
        :param arguments:
        :return: pika 框架生成的随机回调队列名
        """
        result = self._channel.queue_declare(
            queue=queue_name,
            passive=passive,
            durable=durable,
            exclusive=exclusive,
            auto_delete=auto_delete,
            arguments=arguments
        )
        print(result.method.queue)
        return result.method.queue

    def declare_basic_consuming(self, queue_name, callback):
        self._channel.basic_consume(
            consumer_callback=callback,
            queue=queue_name
        )

    def declare_default_consuming(self, queue_name, callback, passive=False,
                                  durable=False,exclusive=False, auto_delete=False,
                                  arguments=None):
        """
        声明一个默认的交换机的队列，并且监听这个队列
        :param queue_name:
        :param callback:
        :return:
        """
        result = self.declare_queue(
            queue_name=queue_name,passive=passive,
            durable=durable,exclusive=exclusive,
            auto_delete=auto_delete,arguments=arguments
        )
        self.declare_basic_consuming(
            queue_name=queue_name,
            callback=callback
        )
        return result

    def declare_consuming(self, exchange_name, routing_key, queue_name, callback):
        """
        声明一个主题交换机队列，并且将队列和交换机进行绑定，同时监听这个队列
        :param exchange_name:
        :param routing_key:
        :param queue_name:
        :param callback:
        :return:
        """
        self.bind_topic_exchange(exchange_name, routing_key, queue_name)
        self.declare_basic_consuming(
            queue_name=queue_name,
            callback=callback
        )

    def consuming(self):
        self._channel.start_consuming()

    def register_class(self, rpc_class):
        if not hasattr(rpc_class,'declare'):
            raise AttributeError("注册的类必须包含 declare 方法")
        self._rpc_class_list.append(rpc_class)

    def accept(self, key, result):
        """
        同步接受确认消息
        :param key: correlation_id
        :param result 服务端返回的消息
        """
        self.data[key]['isAccept'] = True
        self.data[key]['result'] = result

    def send(self, body, exchange, key, corr_id=None):
        if None:
            self._channel.basic_publish(
                exchange=exchange,
                routing_key=key,
                body=body
            )
        else:
            self._channel.basic_publish(
                exchange=exchange,
                routing_key=key,
                body=body,
                properties=pika.BasicProperties(
                    correlation_id=corr_id
                )
            )

    def send_json(self, body, exchange, key, corr_id=None):
        data = json.dumps(body)
        self.send(data, exchange=exchange, key=key, corr_id=corr_id)

    def send_sync(self, body, exchange, key):
        """
        发送并同步接受回复消息
        :return:
        """
        print("1")
        result = self._channel.queue_declare(exclusive=True)
        print("2")
        self.callback_queue = result.method.queue  # 得到随机回调队列名
        print("3")
        self._channel.basic_consume(self.on_response,   # 客户端消费回调队列
                                    no_ack=True,
                                    queue=self.callback_queue)
        print("4")

        corr_id = str(uuid.uuid4())  # 生成客户端请求id
        self.data[corr_id] = {
            'isAccept': False,
            'result': None
        }
        self._channel.basic_publish( # 发送数据给服务端
            exchange=exchange,
            routing_key=key,
            body=body,
            properties=pika.BasicProperties(
                reply_to=self.callback_queue,
                correlation_id=corr_id,
            )
        )
        print("5")
        while not self.data[corr_id]['isAccept']:  # 判断是否接收到服务端返回的消息
            print("process_data_events....")
            self._connection.process_data_events()
            time.sleep(0.5)
            continue
        logger.info("Got the RPC server response => {}".format(self.data[corr_id]['result']))
        return self.data[corr_id]['result']

    def on_response(self, ch, method, props, body):
        corr_id = props.correlation_id  # 从props得到服务端返回的客户度传入的corr_id值
        info_dict = self.data[corr_id]  # 得到对应corr_id存储的客户端信息和是否接受过的对象
        info_dict['isAccept'] = True    # 设置为已经接受到服务端返回的消息
        info_dict['result'] = str(body)

        self._channel.close()

    def send_json_sync(self, body, exchange, key):
        data = json.dumps(body)
        return self.send_sync(data, exchange=exchange, key=key)

    def run(self):
        # 进行注册和声明
        for item in self._rpc_class_list:
            item().declare()
        for (type, queue_name, exchange_name, routing_key, callback) in self.queue._rpc_class_list:
            if type == ExchangeType.DEFAULT:
                self.declare_default_consuming(
                    queue_name=queue_name,
                    callback=callback
                )
            if type == ExchangeType.TOPIC:
                self.declare_consuming(
                    queue_name=queue_name,
                    exchange_name=exchange_name,
                    routing_key=routing_key,
                    callback=callback
                )
        logger.info("consuming...")
        t = threading.Thread(target = self.consuming)
        t.start()