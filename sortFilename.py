import os , shutil

def filesetFind(directory):
    if os.path.isdir(directory):
        fileset = os.listdir(directory)
    elif directory == 'CWD' or directory == 'cwd' or directory == '.':
        fileset = os.listdir(os.getcwd())
    else:
        print("Not a directory.")
    return fileset

def fileOperations(fileset , directory):
    dirList = list(dict.fromkeys([x.split(seperator , 1)[0] for x in fileset]))
    cwd = os.getcwd()
    for i in dirList:
        os.makedirs(f"{cwd}/head/{i}")
        for x in fileset:
            if i in x:
                shutil.copy(f"{directory}/{x}" , f"{cwd}/head/{i}/{x}")
            else:
                pass

while True:
    seperator = input('File seperator? ')
    directory = input('Where am I looking? ')
    files = filesetFind(directory)
    fileOperations(files , directory)
