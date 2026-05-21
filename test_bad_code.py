import pickle, os

password = "hunter2"  # hardcoded
os.system("ls " + user_input)  # shell injection

def load(data):
    return pickle.loads(data)  # unsafe deserialize